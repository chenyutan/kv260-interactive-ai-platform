"""Synthetic poses only; these are not camera, board, or human acceptance."""
import math
import random
import unittest

from platformer.controls import CONTROL_HINT, PoseControls


ASPECT = 16 / 9
COMFORT_RIGHT = (.25, .65)


def pose(right=(0., .50), left=(0., .50), score=.80, scale=1.):
    """Arm offsets in shoulder widths, already in mirrored screen space."""
    p = [[.5, .5, 0.] for _ in range(17)]
    shoulders = {5: (.40, .40), 6: (.60, .40)}
    width = (.60 - .40) * ASPECT * scale
    centers = {i: (.50 + (xy[0] - .50) * scale, xy[1]) for i, xy in shoulders.items()}
    for shoulder, elbow, wrist, offsets in ((6, 8, 10, right), (5, 7, 9, left)):
        sx, sy = centers[shoulder]
        dx, dy = offsets
        wx, wy = sx - dx * width / ASPECT, sy + dy * width
        # Low hands have a naturally bent elbow just below the wrist. Above
        # shoulder/side gestures use the former connected-arm construction.
        if dy >= .32:
            ex = sx - (dx * .35 + .16) * width / ASPECT
            ey = sy + (dy + .12) * width
        else:
            ex = sx - (dx * .48 + (.16 if abs(dx) < .15 else 0)) * width / ASPECT
            ey = sy + (dy * .48 + (.10 if abs(dy) < .1 else 0)) * width
        p[shoulder] = [sx, sy, score]
        p[elbow] = [ex, ey, score]
        p[wrist] = [wx, wy, score]
    p[11], p[12] = [centers[5][0], .73, score], [centers[6][0], .73, score]
    return p


def packet(t, p=None, sequence=None, pid=1, **fields):
    result = dict(pid=pid, sequence=round(t * 1000) if sequence is None else sequence,
                  timestamp_seconds=t, points17=pose() if p is None else p)
    result.update(fields)
    return result


def missing_arm(points, hand):
    """Keep global shoulders, remove this arm's observations."""
    for index in ((8, 10) if hand == 'right' else (7, 9)):
        points[index][2] = 0.
    return points


def calibrated_packets(start=10., *, right=COMFORT_RIGHT, left=(0., .50),
                       score=.80, scale=1., fps=30., duration=.80):
    """Fresh comfortable observations for integration/native test setup."""
    return [(start + i / fps, packet(start + i / fps,
            pose(right, left, score, scale)))
            for i in range(round(duration * fps) + 1)]


def calibrated_control(start=10., *, right=COMFORT_RIGHT, left=(0., .50),
                       score=.80, scale=1., fps=30., confirm_context=False, control=None):
    """Return a proven adapter and last source time; no synthetic gesture fires."""
    control = PoseControls() if control is None else control
    frames = calibrated_packets(start, right=right, left=left, score=score,
                                scale=scale, fps=fps)
    for t, state in frames:
        control.update(state, t, confirm_context=confirm_context)
    return control, frames[-1][0]


def translated(points, x=.0, y=.0):
    return [[p[0] + x, p[1] + y, p[2]] for p in points]


def tilted(points, angle):
    """Rotate all points about the shoulder center in physical image units."""
    result = []
    cosine, sine = math.cos(angle), math.sin(angle)
    for x, y, score in points:
        dx, dy = (x - .5) * ASPECT, y - .4
        result.append([.5 + (dx * cosine - dy * sine) / ASPECT,
                       .4 + dx * sine + dy * cosine, score])
    return result


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.control = PoseControls()
        self.t = 10.
        self.last_packet = None

    def feed(self, right=COMFORT_RIGHT, left=(0., .50), *, dt=.05,
             context=False, points=None, fields=None, **kwargs):
        self.t += dt
        self.last_packet = packet(self.t, pose(right, left, **kwargs) if points is None else points,
                                  **(fields or {}))
        return self.control.update(self.last_packet, self.t, confirm_context=context)

    def ready(self, right=COMFORT_RIGHT, **kwargs):
        events = [self.feed(right=right, **kwargs) for _ in range(17)]
        self.assertEqual(events[-1]['calibration'], 'ready')
        return events

    def moving(self, direction=1, **kwargs):
        right = (COMFORT_RIGHT[0] + direction * .32, COMFORT_RIGHT[1])
        self.feed(right=right, dt=.06, **kwargs)
        result = self.feed(right=right, dt=.06, **kwargs)
        self.assertEqual(result['move'], direction)
        return result

    def lift(self, right=COMFORT_RIGHT, **kwargs):
        return [self.feed(right=right, left=(0., y), **kwargs)
                for y in (.40, .22, .06, -.08, -.15, -.20, -.20)]

    def centered_confirmation(self, context='title'):
        result = None
        for _ in range(8 if context in ('title', 'terminal') else 6):
            result = self.feed(context=context)
        self.assertTrue(result['confirm_ready'])
        return result

    def press(self, context='title', count=8, right=None, **kwargs):
        right = (COMFORT_RIGHT[0], COMFORT_RIGHT[1] + .38) if right is None else right
        return [self.feed(right=right, context=context, **kwargs) for _ in range(count)]

    def test_calibration_is_personal_and_not_body_middle(self):
        for center in ((-.25, .60), (.25, .65), (.65, .75)):
            self.control, self.t = calibrated_control(right=center)
            result = self.feed(right=center)
            self.assertEqual(result['move'], 0)
            self.assertAlmostEqual(result['joystick_dx'], 0., places=6)
            self.assertAlmostEqual(result['joystick_dy'], 0., places=6)
            self.assertTrue(result['jump_ready'])

    def test_calibration_requires_continuous_stable_new_frames_for_700ms(self):
        events = self.ready()
        self.assertTrue(all(r['move'] == 0 for r in events))
        self.assertTrue(all(r['calibration'] == 'calibrating' for r in events[:14]))
        self.assertEqual(events[14]['calibration'], 'ready')
        self.assertLess(events[10]['calibration_progress'], 1.)
        self.assertFalse(any(r['jump'] or r['confirm'] for r in events))

    def test_duplicate_packets_cannot_advance_calibration(self):
        self.feed()
        p = self.last_packet
        for i in range(1, 20):
            result = self.control.update(p, self.t + i * .009)
            self.assertNotEqual(result['calibration'], 'ready')
            self.assertEqual(result['calibration_progress'], 0.)

    def test_raised_extended_or_dropped_right_hand_cannot_initially_calibrate(self):
        for right in ((.25, -.20), (.25, .10), (1.10, .50), (.25, 1.55)):
            self.control = PoseControls()
            for _ in range(22):
                result = self.feed(right=right)
            self.assertEqual(result['calibration'], 'waiting', right)
            self.assertEqual(result['move'], 0)

    def test_projected_straight_low_arm_can_define_personal_center(self):
        for _ in range(22):
            p = pose(COMFORT_RIGHT)
            p[8][:2] = [(p[6][i] + p[10][i]) / 2 for i in range(2)]
            result = self.feed(points=p)
        self.assertEqual(result['calibration'], 'ready')
        self.assertAlmostEqual(result['joystick_dx'], 0.)
        self.assertAlmostEqual(result['joystick_dy'], 0.)

    def test_calibration_motion_and_missing_point_restart_proof(self):
        for kind in ('moving', 'missing'):
            self.control = PoseControls()
            for _ in range(10):
                self.feed()
            p = missing_arm(pose(COMFORT_RIGHT), 'right') if kind == 'missing' else pose((.50, .65))
            self.feed(points=p)
            events = [self.feed() for _ in range(10)]
            self.assertNotEqual(events[-1]['calibration'], 'ready', kind)
            for _ in range(8):
                result = self.feed()
            self.assertEqual(result['calibration'], 'ready', kind)

    def test_small_low_confidence_jitter_can_calibrate_and_steer(self):
        rng = random.Random(71)
        for _ in range(24):
            self.feed(right=(.25 + rng.uniform(-.01, .01), .65 + rng.uniform(-.01, .01)), score=.025)
        self.assertEqual(self.control.calibration, 'ready')
        events = [self.feed(right=(.57 + rng.uniform(-.01, .01), .65), score=.025)
                  for _ in range(12)]
        self.assertTrue(all(r['valid'] for r in events))
        self.assertTrue(all(r['move'] == 1 for r in events[2:]))

    def test_small_joystick_moves_both_mirrored_screen_directions(self):
        for direction in (-1, 1):
            self.control, self.t = calibrated_control()
            result = self.moving(direction)
            self.assertAlmostEqual(result['joystick_dx'], direction * .32)
            self.assertFalse(result['jump'] or result['confirm'])

    def test_unmirrored_configuration_flips_screen_direction_after_personal_calibration(self):
        self.control, self.t = calibrated_control(control=PoseControls(mirrored=False))
        for _ in range(3):
            result = self.feed(right=(.57, .65))
        self.assertEqual(result['move'], -1)
        self.assertAlmostEqual(result['joystick_dx'], -.32)

    def test_direction_deadzone_hysteresis_and_immediate_center_stop(self):
        self.ready()
        for _ in range(5):
            self.assertEqual(self.feed(right=(.45, .65))['move'], 0)
        self.moving()
        for x in (.46, .44, .41):
            self.assertEqual(self.feed(right=(x, .65))['move'], 1)
        self.assertEqual(self.feed(right=(.37, .65))['move'], 0)

    def test_direction_reversal_stops_old_direction_before_new_debounce(self):
        self.ready()
        self.moving()
        self.assertEqual(self.feed(right=(-.07, .65), dt=.08)['move'], 0)
        self.assertEqual(self.feed(right=(-.07, .65), dt=.06)['move'], -1)

    def test_returning_or_lowering_hand_stops_immediately(self):
        for right in ((.25, .65), (.57, 1.12), (.57, .35)):
            self.control, self.t = calibrated_control()
            self.moving()
            result = self.feed(right=right)
            self.assertEqual(result['move'], 0)
            self.assertFalse(result['confirm'])

    def test_held_side_never_recalibrates_to_zero(self):
        self.ready()
        self.moving()
        for _ in range(100):
            result = self.feed(right=(.57, .65))
            self.assertEqual(result['move'], 1)
            self.assertAlmostEqual(result['joystick_dx'], .32)
            self.assertEqual(result['calibration'], 'ready')

    def test_translation_and_mild_shoulder_tilt_move_zero_with_body(self):
        self.ready()
        for i in range(1, 6):
            p = translated(tilted(pose(COMFORT_RIGHT), i * .015), x=i * .008, y=i * .004)
            result = self.feed(points=p)
            self.assertEqual(result['calibration'], 'ready')
            self.assertEqual(result['move'], 0)
            self.assertAlmostEqual(result['joystick_dx'], 0., places=6)
            self.assertAlmostEqual(result['joystick_dy'], 0., places=6)

    def test_small_shoulder_width_variation_does_not_recalibrate(self):
        self.ready()
        for scale in (1.04, .97, 1.08, .94, 1.):
            result = self.feed(scale=scale)
            self.assertEqual(result['calibration'], 'ready')
            self.assertAlmostEqual(result['joystick_dx'], 0., places=6)

    def test_moderate_rotation_freezes_and_recovers_without_relearning(self):
        self.ready()
        self.moving()
        result = self.feed(right=(.57, .65), scale=.80)
        self.assertEqual(result['move'], 0)
        self.assertEqual(result['calibration'], 'frozen')
        self.assertFalse(result['jump_ready'] or result['confirm_ready'])
        for _ in range(5):
            result = self.feed()
        self.assertEqual(result['calibration'], 'ready')
        self.assertAlmostEqual(result['joystick_dx'], 0.)

    def test_obvious_geometry_change_discards_center_but_never_relearns_walking_side(self):
        self.ready()
        self.moving()
        result = self.feed(right=(.57, .65), scale=.60)
        self.assertEqual(result['calibration'], 'waiting')
        self.assertEqual(result['reason'], 'body_changed')
        for _ in range(30):
            result = self.feed(right=(.57, .65), scale=.60)
            self.assertEqual(result['move'], 0)
            self.assertEqual(result['calibration'], 'waiting')
        for _ in range(18):
            result = self.feed(scale=.60)
        self.assertEqual(result['calibration'], 'ready')

    def test_body_size_normalization_preserves_small_joystick(self):
        for scale in (.50, .80, 1.20):
            self.control, self.t = calibrated_control(scale=scale)
            result = self.moving(scale=scale)
            self.assertAlmostEqual(result['joystick_dx'], .32)

    def test_left_slow_crossing_shoulder_line_jumps_once(self):
        self.ready()
        events = [self.feed(left=(0., .40 - i * .025)) for i in range(28)]
        self.assertEqual(sum(r['jump'] for r in events), 1)
        self.assertFalse(any(r['confirm'] or r['move'] for r in events))

    def test_left_position_jump_works_at_ten_and_fifteen_source_fps(self):
        for fps in (10, 15):
            self.control, self.t = calibrated_control(fps=fps)
            events = self.lift(dt=1 / fps)
            self.assertEqual(sum(r['jump'] for r in events), 1)

    def test_held_left_hand_jumps_only_once_and_initially_raised_never_jumps(self):
        self.ready()
        events = self.lift() + [self.feed(left=(0., -.20)) for _ in range(30)]
        self.assertEqual(sum(r['jump'] for r in events), 1)
        self.control = PoseControls()
        events = [self.feed(left=(0., -.20)) for _ in range(24)]
        self.assertFalse(any(r['jump'] or r['jump_ready'] for r in events))

    def test_lowering_just_below_shoulder_rearms_without_lowering_to_waist(self):
        self.ready()
        events = self.lift()
        for _ in range(3):
            self.feed(left=(0., .06))
        events += [self.feed(left=(0., -.12)) for _ in range(5)]
        self.assertEqual(sum(r['jump'] for r in events), 2)

    def test_left_shoulder_line_jitter_does_not_fire_repeatedly(self):
        self.ready()
        events = [self.feed(left=(0., y)) for y in (.02, -.02, .01, -.04, .02, -.01) * 4]
        self.assertFalse(any(r['jump'] for r in events))

    def test_short_raise_is_not_proof_and_duplicates_cannot_finish_jump(self):
        self.ready()
        self.feed(left=(0., -.10), dt=.10)
        p = self.last_packet
        for i in range(1, 15):
            self.assertFalse(self.control.update(p, self.t + i * .009)['jump'])
        self.t += .15
        self.assertFalse(self.feed(left=(0., .06))['jump'])

    def test_jump_pulse_is_not_reissued_when_ui_repeats_source_frame(self):
        self.ready()
        for y in (.30, .10, -.10, -.20):
            result = self.feed(left=(0., y))
            if result['jump']:
                break
        self.assertTrue(result['jump'])
        for i in range(1, 12):
            result = self.control.update(self.last_packet, self.t + i * .01)
            self.assertFalse(result['jump'] or result['confirm'])

    def test_left_jump_and_right_small_joystick_are_independent_in_both_directions(self):
        for direction in (-1, 1):
            self.control, self.t = calibrated_control()
            self.moving(direction)
            right = (.25 + direction * .32, .65)
            events = self.lift(right=right)
            self.assertEqual(sum(r['jump'] for r in events), 1)
            self.assertTrue(all(r['move'] == direction for r in events))
            self.assertFalse(any(r['confirm'] for r in events))

    def test_right_raise_and_two_arm_raise_do_not_suppress_left_jump(self):
        self.ready()
        events = []
        for y in (.40, .20, .02, -.10, -.20, -.30, -.30):
            events.append(self.feed(right=(.25, y), left=(0., y), context='title'))
        self.assertEqual(sum(r['jump'] for r in events), 1)
        self.assertFalse(any(r['confirm'] or r['move'] for r in events))

    def test_right_vertical_motion_cannot_emit_left_jump(self):
        self.ready()
        events = [self.feed(right=(.25, .50 - i * .08)) for i in range(14)]
        self.assertFalse(any(r['jump'] or r['confirm'] for r in events))

    def test_left_sideways_motion_below_shoulder_does_not_jump_or_steer(self):
        self.ready()
        events = [self.feed(left=(min(.90, i * .07), .08)) for i in range(15)]
        self.assertFalse(any(r['jump'] or r['move'] for r in events))

    def test_left_can_jump_while_right_missing_or_implausible(self):
        for kind in ('missing', 'implausible'):
            self.control, self.t = calibrated_control()
            events = []
            for y in (.40, .22, .06, -.08, -.15, -.20):
                p = pose(COMFORT_RIGHT, (0., y))
                if kind == 'missing':
                    missing_arm(p, 'right')
                else:
                    p[8][:2] = [0., 0.]
                events.append(self.feed(points=p))
            self.assertEqual(sum(r['jump'] for r in events), 1, kind)
            self.assertTrue(all(r['valid'] and r['move'] == 0 for r in events))

    def test_left_missing_does_not_stop_right_or_prevent_right_confirmation(self):
        self.ready()
        for _ in range(4):
            result = self.feed(points=missing_arm(pose((.57, .65)), 'left'))
        self.assertEqual(result['move'], 1)
        for _ in range(8):
            result = self.feed(points=missing_arm(pose(COMFORT_RIGHT), 'left'), context='terminal')
        self.assertTrue(result['confirm_ready'])
        events = [self.feed(points=missing_arm(pose((.25, 1.03)), 'left'), context='terminal')
                  for _ in range(8)]
        self.assertEqual(sum(r['confirm'] for r in events), 1)

    def test_left_recovery_requires_new_lowered_frames_before_jump(self):
        self.ready()
        self.feed(points=missing_arm(pose(COMFORT_RIGHT), 'left'))
        for _ in range(8):
            result = self.feed(left=(0., -.20))
            self.assertFalse(result['jump'] or result['jump_ready'])
        self.feed(left=(0., .06))
        self.feed(left=(0., .06))
        events = [self.feed(left=(0., -.12)) for _ in range(5)]
        self.assertEqual(sum(r['jump'] for r in events), 1)

    def test_impossible_left_wrist_teleport_does_not_jump_but_right_keeps_moving(self):
        self.ready()
        self.moving()
        events = [self.feed(right=(.57, .65), left=(0., -.90), dt=.04) for _ in range(10)]
        self.assertTrue(all(r['valid'] and r['move'] == 1 for r in events))
        self.assertFalse(any(r['jump'] or r['confirm'] for r in events))

    def test_right_arm_short_loss_preserves_zero_and_reacquires_with_fresh_debounce(self):
        self.ready()
        self.moving()
        result = self.feed(points=missing_arm(pose((.57, .65)), 'right'))
        self.assertEqual(result['move'], 0)
        self.assertEqual(result['calibration'], 'ready')
        self.assertEqual(self.feed(right=(.57, .65))['move'], 0)
        self.assertEqual(self.feed(right=(.57, .65))['move'], 1)

    def test_confirmation_requires_allowed_context_stable_rest_then_downward_press(self):
        for context in ('title', 'terminal', 'pipe', True):
            self.control, self.t = calibrated_control()
            self.centered_confirmation(context)
            events = self.press(context)
            self.assertEqual(sum(r['confirm'] for r in events), 1, context)
            self.assertTrue(events[0]['confirm_pending'])
            self.assertFalse(events[-1]['confirm_pending'] or events[-1]['confirm_ready'])
            self.assertTrue(all(r['move'] == 0 for r in events))

    def test_ordinary_game_downward_press_only_stops_movement(self):
        for context in (False, None, 'playing', 'near_pipe', 1):
            self.control, self.t = calibrated_control()
            self.moving()
            for _ in range(8):
                self.feed(context=context)
            events = self.press(context)
            self.assertFalse(any(r['confirm'] or r['confirm_pending'] for r in events))
            self.assertTrue(all(r['move'] == 0 for r in events))

    def test_acquiring_or_holding_downward_hand_cannot_confirm(self):
        self.ready()
        events = self.press('title', count=24)
        self.assertFalse(any(r['confirm'] or r['confirm_pending'] for r in events))

    def test_centered_confirmation_needs_new_frames_and_200ms(self):
        self.ready()
        self.feed(context='pipe')
        p = self.last_packet
        for i in range(1, 18):
            result = self.control.update(p, self.t + i * .009, confirm_context='pipe')
            self.assertFalse(result['confirm_ready'])
        self.t += .16
        self.assertFalse(self.feed(context='pipe', dt=.01)['confirm_ready'])
        self.assertFalse(self.feed(context='pipe', dt=.01)['confirm_ready'])
        self.assertTrue(self.feed(context='pipe', dt=.03)['confirm_ready'])

    def test_confirmation_press_requires_two_new_frames_and_120ms(self):
        self.ready()
        self.centered_confirmation()
        self.feed(right=(.25, 1.03), context='title')
        p = self.last_packet
        for i in range(1, 15):
            self.assertFalse(self.control.update(p, self.t + i * .01, confirm_context='title')['confirm'])
        self.t += .14
        self.assertTrue(self.feed(right=(.25, 1.03), context='title', dt=.01)['confirm'])

    def test_confirmation_requires_return_to_rest_before_each_press(self):
        self.ready()
        total = 0
        for _ in range(2):
            self.centered_confirmation()
            total += sum(r['confirm'] for r in self.press())
        self.assertEqual(total, 2)

    def test_sideways_or_upward_departure_cancels_confirmation_readiness(self):
        for right in ((.61, .65), (.25, .44)):
            self.control, self.t = calibrated_control()
            self.centered_confirmation()
            self.feed(right=right, context='title')
            self.assertFalse(any(r['confirm'] for r in self.press()))

    def test_missing_right_geometry_or_transport_cancels_confirmation_proof(self):
        for kind in ('right', 'shoulder', 'missing'):
            self.control, self.t = calibrated_control()
            self.centered_confirmation()
            self.feed(right=(.25, 1.03), context='title')
            if kind == 'missing':
                self.control.update(None, self.t + .01, confirm_context='title')
            else:
                p = pose((.25, 1.03))
                if kind == 'right': missing_arm(p, 'right')
                else: p[5][2] = 0.
                self.feed(points=p, context='title')
            self.assertFalse(any(r['confirm'] for r in self.press()))

    def test_context_changes_even_on_duplicate_cancel_pending_and_never_accept_held_press(self):
        self.ready()
        self.centered_confirmation()
        result = self.feed(right=(.25, 1.03), context='title')
        self.assertTrue(result['confirm_pending'])
        result = self.control.update(self.last_packet, self.t + .01, confirm_context=False)
        self.assertFalse(result['confirm_pending'] or result['confirm_ready'])
        events = self.press('pipe', count=12)
        self.assertFalse(any(r['confirm'] or r['confirm_pending'] for r in events))

    def test_two_raised_arms_and_legacy_keyword_do_not_confirm(self):
        self.ready()
        for i in range(15):
            self.t += .05
            y = max(-.30, .50 - i * .10)
            result = self.control.update(packet(self.t, pose((.25, y), (0., y))), self.t,
                                         confirm_context='title', allow_held_confirm=True)
            self.assertFalse(result['confirm'] or result['confirm_pending'])

    def test_left_jump_and_right_confirm_may_fire_independently(self):
        self.ready()
        self.centered_confirmation()
        self.feed(left=(0., .15), context='title')
        events = self.press(left=(0., -.12))
        self.assertEqual(sum(r['confirm'] for r in events), 1)
        self.assertEqual(sum(r['jump'] for r in events), 1)

    def test_missing_stale_or_unavailable_pose_stops_every_action_immediately(self):
        for state in (None, dict(stale=True), dict(available=False)):
            self.control, self.t = calibrated_control()
            self.moving()
            result = self.control.update(state, self.t + .01)
            self.assertFalse(result['valid'])
            self.assertEqual(result['move'], 0)
            self.assertFalse(result['jump'] or result['confirm'] or result['jump_ready'])

    def test_duplicate_briefly_holds_then_times_out_without_repeating_events(self):
        self.ready()
        self.moving()
        for dt in (.01, .04, .12):
            result = self.control.update(self.last_packet, self.t + dt)
            self.assertEqual(result['move'], 1)
            self.assertFalse(result['jump'] or result['confirm'])
        result = self.control.update(self.last_packet, self.t + .21)
        self.assertFalse(result['valid'])
        self.assertEqual(result['move'], 0)

    def test_same_packet_cannot_reacquire_mutated_missing_points(self):
        self.ready()
        self.moving()
        result = self.feed(points=missing_arm(pose((.57, .65)), 'right'))
        self.last_packet['points17'] = pose((.57, .65), (0., -.20))
        repeated = self.control.update(self.last_packet, self.t + .01)
        self.assertEqual(repeated['move'], result['move'])
        self.assertFalse(repeated['jump'] or repeated['confirm'])

    def test_equal_stamp_or_equal_sequence_and_out_of_order_do_not_rewind_watermark(self):
        for state in (packet(10., sequence=2), packet(10.1, sequence=1)):
            c = PoseControls()
            c.update(packet(10., sequence=1), 10.)
            result = c.update(state, 10.1)
            self.assertEqual(result['reason'], 'out_of_order_pose')
        self.ready()
        self.assertFalse(self.control.update(packet(self.t - .02), self.t + .01)['valid'])
        self.assertFalse(self.control.update(self.last_packet, self.t + .02)['valid'])
        self.assertTrue(self.feed()['valid'])

    def test_failed_new_frame_supersedes_old_good_frame(self):
        self.ready()
        self.moving()
        old = self.last_packet
        self.t += .05
        self.control.update(packet(self.t, available=False), self.t)
        self.assertFalse(self.control.update(old, self.t + .01)['valid'])

    def test_old_future_and_bad_latency_sources_are_rejected_on_first_receipt(self):
        for state, now in ((packet(1.), 10.), (packet(10.), 9.),
                           (packet(10., latency_ms=900), 10.),
                           (packet(10., latency_ms=math.nan), 10.),
                           (packet(10., latency_ms=-1), 10.)):
            self.assertFalse(PoseControls().update(state, now)['valid'])

    def test_remote_clock_is_explicit_and_requires_finite_latency(self):
        c = PoseControls(shared_clock=False)
        self.assertTrue(c.update(packet(1., latency_ms=120), 100.)['valid'])
        self.assertFalse(c.update(packet(1.1), 100.1)['valid'])
        self.assertFalse(c.update(packet(1.2, latency_ms=900), 100.2)['valid'])

    def test_pause_reset_keeps_center_but_requires_new_center_and_low_left_frames(self):
        self.ready()
        self.moving()
        self.control.reset()
        self.assertFalse(self.control.update(self.last_packet, self.t + .01)['valid'])
        for _ in range(8):
            result = self.feed(right=(.57, .65), left=(0., -.20))
            self.assertEqual(result['calibration'], 'ready')
            self.assertEqual(result['move'], 0)
            self.assertFalse(result['jump'] or result['confirm'])
        for _ in range(4):
            self.feed()
        self.moving()

    def test_explicit_reset_can_discard_calibration_without_rewinding_watermark(self):
        self.ready()
        self.control.reset(preserve_calibration=False)
        self.assertFalse(self.control.update(self.last_packet, self.t + .01)['valid'])
        result = self.feed()
        self.assertEqual(result['calibration'], 'calibrating')

    def test_person_pid_and_stream_change_clear_calibration_and_jump_proofs(self):
        changes = ({'pid': 2}, {'person_id': 'other'}, {'stream_reset': True},
                   {'roi_selection': {'motion_history_discontinuity': True}})
        for fields in changes:
            self.control, self.t = calibrated_control()
            result = self.feed(left=(0., -.20), fields=fields)
            self.assertNotEqual(result['calibration'], 'ready')
            self.assertFalse(result['jump'] or result['confirm'] or result['move'])

    def test_source_or_delivery_gap_clears_actions_and_needs_new_low_left_observation(self):
        for remote in (False, True):
            self.control, self.t = calibrated_control()
            if remote: self.control.shared_clock = False
            self.t += .35
            state = packet(self.t, pose(COMFORT_RIGHT, (0., -.20)), latency_ms=10)
            result = self.control.update(state, self.t)
            self.assertFalse(result['jump'] or result['confirm'])
            self.assertEqual(result['calibration'], 'ready')

    def test_invalid_clock_or_rollback_stops_and_clears_actions(self):
        self.ready()
        self.moving()
        self.assertFalse(self.control.update(self.last_packet, math.nan)['valid'])
        self.feed()
        result = self.control.update(packet(self.t - .1), self.t - .1)
        self.assertEqual(result['reason'], 'clock_rollback')
        self.assertEqual(result['calibration'], 'waiting')

    def test_invalid_numbers_geometry_and_zero_shoulders_never_manufacture_controls(self):
        for kind in ('nan', 'outside', 'limb', 'shoulder', 'zero'):
            p = pose(COMFORT_RIGHT)
            if kind == 'nan': p[10][0] = math.nan
            elif kind == 'outside': p[10][1] = 1.3
            elif kind == 'limb': p[8][:2] = [0., 0.]
            elif kind == 'shoulder': p[5][:2] = p[6][:2]
            else: p[6][2] = 0.
            result = PoseControls().update(packet(10., p), 10.)
            self.assertEqual(result['valid'], kind not in ('shoulder', 'zero'), kind)
            self.assertEqual(result['move'], 0)
            self.assertFalse(result['jump'] or result['confirm'])
        for p in (None, [], {}, [[0., 0., .9]] * 16):
            state = packet(10.)
            state['points17'] = p
            self.assertFalse(PoseControls().update(state, 10.)['valid'])

    def test_both_arms_missing_disables_every_control(self):
        self.ready()
        p = missing_arm(missing_arm(pose(COMFORT_RIGHT), 'left'), 'right')
        result = self.feed(points=p)
        self.assertFalse(result['valid'])
        self.assertEqual(result['move'], 0)
        self.assertFalse(result['jump'] or result['confirm'])

    def test_movement_calibration_tolerates_low_rest_noise_and_mild_shoulder_variation(self):
        for i in range(25):
            right = (.25 + (.075 if i % 2 else -.075), .65 + (.07 if i % 2 else -.07))
            points = tilted(pose(right, scale=1.06 if i % 2 else .94), .06 if i % 2 else -.06)
            result = self.feed(points=points)
        self.assertEqual(result['calibration'], 'ready')
        self.assertFalse(result['jump'] or result['confirm'])

    def test_movement_reset_accepts_near_rest_without_relearning_zero(self):
        self.ready()
        self.control.reset()
        for _ in range(4):
            result = self.feed(right=(.45, .85))
        self.assertEqual(result['move'], 0)
        self.assertAlmostEqual(result['joystick_dx'], .20)
        self.assertAlmostEqual(result['joystick_dy'], .20)
        self.moving()

    def test_entry_can_start_without_movement_calibration_and_learns_prepress_anchor(self):
        for context in ('title', 'terminal'):
            self.control = PoseControls()
            for _ in range(8):
                result = self.feed(right=(1.15, .65), context=context)
            self.assertNotEqual(result['calibration'], 'ready')
            self.assertTrue(result['confirm_ready'])
            events = self.press(context, right=(1.15, 1.03))
            self.assertEqual(sum(r['confirm'] for r in events), 1)
            self.assertFalse(any(r['jump'] for r in events))
            result = self.feed(right=(1.15, .65), context=False)
            self.assertAlmostEqual(result['joystick_dx'], 0.)
            self.assertAlmostEqual(result['joystick_dy'], 0.)
            self.assertEqual(result['calibration'], 'ready')

    def test_entry_uses_current_rest_far_from_old_center_and_after_reset(self):
        for context in ('title', 'terminal'):
            self.control, self.t = calibrated_control()
            self.control.reset()
            for _ in range(8):
                result = self.feed(right=(.95, .65), context=context, dt=.07)
            self.assertTrue(result['confirm_ready'])
            self.assertAlmostEqual(result['joystick_dx'], .70)
            events = self.press(context, right=(.95, 1.03))
            self.assertEqual(sum(r['confirm'] for r in events), 1)
            result = self.feed(right=(.95, .65), context=False)
            self.assertAlmostEqual(result['joystick_dx'], 0.)

    def test_entry_can_recover_from_old_movement_geometry_freeze(self):
        self.ready()
        for _ in range(8):
            result = self.feed(right=(.95, .65), scale=.80, context='title', dt=.07)
        self.assertEqual(result['calibration'], 'frozen')
        self.assertTrue(result['confirm_ready'])
        events = self.press(right=(.95, 1.03), scale=.80)
        self.assertEqual(sum(r['confirm'] for r in events), 1)
        result = self.feed(right=(.95, .65), scale=.80)
        self.assertEqual(result['calibration'], 'ready')
        self.assertAlmostEqual(result['joystick_dx'], 0.)

    def test_entry_anchor_needs_300ms_and_four_distinct_frames(self):
        self.feed(context='title', dt=.05)
        original = self.last_packet
        for i in range(1, 18):
            result = self.control.update(original, self.t + i * .009, confirm_context='title')
            self.assertFalse(result['confirm_ready'])
        self.t += .16
        self.assertFalse(self.feed(context='title', dt=.01)['confirm_ready'])
        self.assertFalse(self.feed(context='title', dt=.13)['confirm_ready'])
        self.assertTrue(self.feed(context='title', dt=.01)['confirm_ready'])

    def test_entry_anchor_accepts_natural_low_positions_and_rejects_raised_or_far_hands(self):
        for right, expected in (((.25, .26), True), ((1.25, .65), True),
                                ((.25, 1.44), True), ((.25, .24), False),
                                ((1.26, .65), False), ((.25, 1.46), False)):
            self.control = PoseControls()
            for _ in range(10):
                result = self.feed(right=right, context='title')
            self.assertEqual(result['confirm_ready'], expected, right)
            self.assertFalse(result['confirm'] or result['jump'])

    def test_entry_anchor_accepts_wider_pose_jitter_than_movement_calibration(self):
        for i in range(8):
            result = self.feed(right=(.75 + (.13 if i % 2 else -.13),
                                      .65 + (.10 if i % 2 else -.10)), context='title')
        self.assertTrue(result['confirm_ready'])
        self.assertNotEqual(result['calibration'], 'ready')
        events = self.press(right=(.75, 1.03))
        self.assertEqual(sum(r['confirm'] for r in events), 1)

    def test_entry_unstable_rest_does_not_arm_or_confirm(self):
        for axis in ('x', 'y'):
            self.control = PoseControls()
            for i in range(24):
                offset = .15 if i % 2 else -.15
                right = (.75 + offset, .65) if axis == 'x' else (.75, .65 + offset)
                result = self.feed(right=right, context='title')
                self.assertFalse(result['confirm_ready'] or result['confirm'], axis)

    def test_entry_relative_press_lane_allows_small_horizontal_drift(self):
        for drift in (-.34, .34):
            self.control = PoseControls()
            for _ in range(8):
                self.feed(right=(.65, .65), context='title')
            events = self.press(right=(.65 + drift, 1.03))
            self.assertEqual(sum(r['confirm'] for r in events), 1, drift)

    def test_entry_large_side_or_upward_departure_cancels_anchor(self):
        for right in ((.61, .65), (.25, .44)):
            self.control = PoseControls()
            self.centered_confirmation()
            result = self.feed(right=right, context='title')
            self.assertFalse(result['confirm_ready'])
            self.assertFalse(any(r['confirm'] for r in self.press()))

    def test_entry_press_threshold_and_minimum_duration_are_deliberate(self):
        self.centered_confirmation()
        self.assertFalse(any(r['confirm'] for r in self.press(right=(.25, .949), count=8)))
        self.assertFalse(self.feed(right=(.25, .95), context='title', dt=.05)['confirm'])
        self.assertFalse(self.feed(right=(.25, .95), context='title', dt=.119)['confirm'])
        result = self.feed(right=(.25, .95), context='title', dt=.001)
        self.assertTrue(result['confirm'])
        self.assertFalse(result['jump'])

    def test_entry_needs_return_near_anchor_before_a_second_press(self):
        self.centered_confirmation()
        events = self.press(count=24)
        self.assertEqual(sum(r['confirm'] for r in events), 1)
        # A small partial lift still leaves the hand below the release threshold.
        events = self.press(right=(.25, .79), count=10) + self.press(count=12)
        self.assertFalse(any(r['confirm'] or r['confirm_ready'] for r in events))
        for _ in range(8):
            result = self.feed(right=(.35, .76), context='title')
        self.assertTrue(result['confirm_ready'])
        events = self.press(right=(.35, 1.14), count=12)
        self.assertEqual(sum(r['confirm'] for r in events), 1)

    def test_entry_new_person_source_stale_and_gap_cancel_pending_press(self):
        for kind in ('person', 'pid', 'stream', 'stale', 'gap'):
            self.control = PoseControls()
            self.centered_confirmation()
            self.feed(right=(.25, 1.03), context='title')
            fields = {}
            if kind == 'person': fields = {'person_id': 'other'}
            elif kind == 'pid': fields = {'pid': 2}
            elif kind == 'stream':
                self.feed(right=(.25, 1.03), context='title', fields={'stream_reset': True})
            elif kind == 'stale':
                self.feed(right=(.25, 1.03), context='title', fields={'stale': True})
            elif kind == 'gap':
                self.feed(right=(.25, 1.03), context='title', dt=.31)
            if fields:
                self.feed(right=(.25, 1.03), context='title', fields=fields)
            events = self.press(count=18, fields=fields)
            self.assertFalse(any(r['confirm'] or r['jump'] for r in events), kind)

    def test_title_to_terminal_context_change_cannot_complete_old_pending_press(self):
        self.centered_confirmation()
        self.feed(right=(.25, 1.03), context='title')
        events = self.press('terminal', count=18)
        self.assertFalse(any(r['confirm'] for r in events))

    def test_pipe_and_boolean_context_still_require_old_precise_center(self):
        for context in ('pipe', True):
            self.control, self.t = calibrated_control()
            for _ in range(10):
                result = self.feed(right=(.95, .65), context=context, dt=.07)
            self.assertFalse(result['confirm_ready'])
            self.assertFalse(any(r['confirm'] for r in self.press(context, right=(.95, 1.03))))
            self.centered_confirmation(context)
            events = self.press(context, right=(.44, 1.03))
            self.assertFalse(any(r['confirm'] for r in events))

    def test_invalid_constructor_parameters_are_rejected(self):
        for kwargs in ({'aspect': 0}, {'aspect': math.nan}, {'hold_seconds': 0},
                       {'hold_seconds': .6}, {'source_max_age': -1}):
            with self.assertRaises(ValueError):
                PoseControls(**kwargs)

    def test_hint_names_small_right_joystick_left_position_jump_and_contextual_press(self):
        self.assertIn('右手小幅左右', CONTROL_HINT)
        self.assertIn('左手抬过肩', CONTROL_HINT)
        self.assertIn('下压', CONTROL_HINT)
        self.assertNotIn('挥动', CONTROL_HINT)
        self.assertNotIn('双臂', CONTROL_HINT)


if __name__ == '__main__':
    unittest.main()
