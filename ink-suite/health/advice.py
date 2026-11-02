"""Small observed-only, temporally stable coaching cues.

These are picture-plane observations, not a comparison with a mandatory ideal
body shape.  They never score health, infer a missing joint, or use the lesson
animation clock to decide that a person is wrong.  The caller must first reject
stale frames, identity changes and tracking discontinuities.
"""
import copy
import math
from functools import lru_cache


EMPTY_ADVICE = dict(code='none', text='', joints=[], evidence={},
                    stable_seconds=0., kind='none')
STRONG_CONFIDENCE = .45
MAX_GAP = .4


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _points(raw):
    """Validate independently as this observer can also be used in local QA."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 17:
        return [None] * 17
    output = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) < 3:
            output.append(None)
            continue
        x, y, confidence = map(_finite, point[:3])
        output.append((x, y, confidence) if
                      x is not None and y is not None and confidence is not None
                      and 0 <= x <= 1 and 0 <= y <= 1 and confidence >= .15
                      else None)
    return output


def _cue(code, text, joints=(), evidence=None, kind='observation', hold=.65):
    return dict(code=code, text=text, joints=list(joints), evidence=evidence or {},
                kind=kind, hold=float(hold))


@lru_cache(maxsize=40)
def _lesson_steps(course_id, lesson_id):
    from . import catalog, taichi_catalog
    module = taichi_catalog if course_id == 'taichi' else catalog
    lesson = next((row for row in module.COURSE if row['id'] == lesson_id), None)
    return tuple(lesson['instructions'][:2]) if lesson else ('按自己的舒适幅度缓慢练习。',)


def lesson_guidance(course_id, lesson_id):
    """A useful course instruction even when no current observation is usable."""
    steps = _lesson_steps(course_id, lesson_id)
    action = steps[1] if len(steps) > 1 else steps[0]
    return dict(code='course_guidance', text='练习建议：'+action,
                display_text='建议：'+action,
                joints=[], evidence={'basis': 'course_steps', 'pose_error_confirmed': False},
                stable_seconds=0., kind='guidance')


def _select(lesson_id, points, required, course_id='baduanjin'):
    cue = _select_observation(lesson_id, points, required, course_id)
    if cue is None:
        cue = dict(lesson_guidance(course_id, lesson_id), hold=.65)
    if cue['kind'] == 'visibility':
        # Visibility advice alone used to leave a learner with no action to do.
        missing = set(cue['evidence'].get('missing_joint_ids', ()))
        part = ('手腕' if missing & {9, 10} else
                '腿部' if missing & {13, 14, 15, 16} else '部分关节')
        cue['text'] = part+'点位暂不清晰；'+lesson_guidance(course_id, lesson_id)['text']
        cue['display_text'] = '参考建议：'+lesson_guidance(course_id, lesson_id)['display_text'][3:]
    weak = [i for i in required if points[i] is not None and points[i][2] < STRONG_CONFIDENCE]
    if weak and cue['kind'] not in ('visibility', 'guidance'):
        # Use the actual current estimates, without promoting their confidence
        # or allowing this advisory path to award repetitions.
        cue['code'] = 'uncertain_'+cue['code']
        action = cue['text'].split('；', 1)[-1]
        if len(action)>28:action=lesson_guidance(course_id, lesson_id)['display_text'][3:]
        cue['display_text'] = '点位偏弱·参考：'+action
        cue['text'] = '仅供参考（点位偏弱）：'+cue['text'].replace('已看到', '可能看到')
        cue['evidence'] = dict(cue['evidence'], uncertain_joint_ids=weak,
                               confidence='low', pose_error_confirmed=False)
        cue['kind'] = 'tentative'
        cue['hold'] = max(.8, cue['hold'])
    return cue


def _select_observation(lesson_id, points, required, course_id='baduanjin'):
    """Choose one cue using current observations only, ordered by usefulness."""
    missing = [i for i in required if points[i] is None]
    if missing:
        if set(missing) & {9, 10}:
            text = '手腕点位暂不完整，请调整取景或避开遮挡；不据此判定动作有误。'
        elif set(missing) & {13, 14, 15, 16}:
            text = '所需腿部关节未完整入镜，可调整取景；暂不评价下肢动作。'
        else:
            text = '肩、肘或髋点位暂不完整，请让所需关节清楚入镜。'
        return _cue('visibility_missing', text, missing,
                    {'missing_joint_ids': missing}, 'visibility', .65)
    edges = [i for i in required if min(points[i][0], 1-points[i][0],
                                       points[i][1], 1-points[i][1]) < .025]
    if edges:
        return _cue('frame_edge', '部分关节接近画面边缘，可稍向画面中央调整，避免出框。',
                    edges, {'edge_joint_ids': edges}, 'visibility', .8)
    if any(points[i] is None for i in (5, 6, 11, 12)):
        return None
    ls, rs, lh, rh = (points[i] for i in (5, 6, 11, 12))
    sy, hy = (ls[1]+rs[1])/2., (lh[1]+rh[1])/2.
    sx, hx = (ls[0]+rs[0])/2., (lh[0]+rh[0])/2.
    torso = math.hypot(sx-hx, sy-hy)
    width = math.hypot(ls[0]-rs[0], ls[1]-rs[1])
    if torso < .055 or width < .025 or torso > .75 or width > .8 or hy-sy < .025:
        return _cue('geometry_uncertain',
                    '当前躯干点位不足以稳定比较，请调整取景后再观察。',
                    (5, 6, 11, 12), kind='visibility', hold=.8)
    ly, ry = points[9][1], points[10][1]
    if course_id == 'taichi':
        return _taichi_select(lesson_id, points, sy, hy, sx, torso, width)
    if lesson_id == 'raise':
        delta = abs(ly-ry)/torso
        # Both hands must have reached the shoulder region.  A brief lead by
        # one hand during ascent/return is not treated as a correction request.
        if max(ly, ry) < sy+.10*torso and min(ly, ry) < sy-.12*torso and delta > .38:
            return _cue('raise_height_difference',
                        '双腕在画面中的高度持续不同；若正在双手上托，可在舒适范围内稍作协调。',
                        (9, 10), {'wrist_height_difference_torso': round(delta, 3)},
                        'suggestion', 1.2)
        if max(ly, ry) < sy-.18*torso:
            return _cue('raise_seen', '已看到双腕上举，按舒适幅度缓缓收回即可。',
                        (9, 10), {'both_wrists_above_shoulders': True})
        if min(ly, ry) > sy+.12*torso:
            return _cue('raise_return', '双腕目前在肩线下方；从身前缓慢上举，不必追求图示高度。',
                        (9, 10), {'both_wrists_below_shoulders': True})
        return _cue('raise_transition', '双腕处于上举或回收的过渡位置；按自己的节奏完成即可。',
                    (9, 10), {'both_wrists_above_shoulders': False})
    if lesson_id == 'bow':
        reach = max(abs(points[9][0]-sx), abs(points[10][0]-sx))/width
        separation = abs(points[9][0]-points[10][0])/width
        deviation = max(abs(ly-sy), abs(ry-sy))/torso
        if reach > 1.1 and separation > 1.35:
            if deviation > .65:
                return _cue('bow_height_difference',
                            '侧向展开时手腕偏离肩线较多；可对照视频调整至舒适高度。',
                            (5, 6, 9, 10), {'wrist_shoulder_distance_torso': round(deviation, 3)},
                            'suggestion', 1.1)
            return _cue('bow_open_seen', '已看到手臂向侧方展开；缓慢收回后再换另一侧。',
                        (9, 10), {'lateral_reach_shoulders': round(reach, 3)})
        return _cue('bow_retracted_seen', '手臂处于准备或回收位置；对照视频完成侧向展开和收回。',
                    (9, 10), {'wrist_separation_shoulders': round(separation, 3)})
    if lesson_id == 'single':
        if max(ly, ry) < sy-.12*torso:
            return _cue('single_both_high',
                        '画面中双腕均已上举；若已完成换侧，可对照视频交替做一上一下。',
                        (9, 10), {'both_wrists_above_shoulders': True}, 'suggestion', 1.2)
        if min(ly, ry) < sy-.18*torso and max(ly, ry) > sy+.20*torso:
            return _cue('single_seen', '已看到一手上举、一手下放；缓缓收回后再换另一侧。',
                        (9, 10), {'opposed_wrist_heights': True})
        return _cue('single_transition', '双腕处于过渡位置；按自己的节奏交替完成，不必追赶示意。',
                    (9, 10), {'opposed_wrist_heights': False})
    if lesson_id == 'sway':
        lean = abs(sx-hx)/torso
        if lean > .23:
            return _cue('sway_seen', '已看到躯干在画面中向一侧移动；缓慢回中后再换侧。',
                        (5, 6, 11, 12), {'planar_torso_offset': round(lean, 3)})
        return _cue('sway_center', '肩部与髋部在画面中接近对齐；只在舒适范围内缓慢侧移。',
                    (5, 6, 11, 12), {'planar_torso_offset': round(lean, 3)})
    if lesson_id == 'bend':
        descent = ((ly+ry)/2.-hy)/torso
        if descent > .60:
            return _cue('bend_descent_seen', '已看到双腕下降；按舒适幅度缓慢回升，不以触足为目标。',
                        (9, 10), {'wrists_below_hips_torso': round(descent, 3)})
        return _cue('bend_ready', '尚未看到双腕持续下降到较低位置；按舒适范围练习，不强行弯腰。',
                    (9, 10), {'wrists_below_hips_torso': round(descent, 3)})
    if lesson_id == 'look':
        return _cue('look_scope', '身体关节已入镜；骨架不能确认头颈朝向，请以视频为准轻缓回望。',
                    (), {'head_direction_observed': False}, 'scope')
    if lesson_id == 'fist':
        return _cue('fist_scope', '上肢关节已入镜；前推深度与握拳暂不判断，请对照视频慢伸慢收。',
                    (), {'hand_shape_observed': False, 'forward_depth_observed': False}, 'scope')
    if lesson_id == 'heel':
        return _cue('heel_scope', '骨架不能确认脚跟起落；对照视频轻缓完成，站不稳时可跳过。',
                    (), {'heel_lift_observed': False}, 'scope')
    if lesson_id == 'finish':
        return _cue('finish_seen', '肩肘腕髋已入镜；双臂自然放松，按自己的节奏休息。',
                    (5, 6, 7, 8, 9, 10, 11, 12), {'required_joints_visible': True})
    return _cue('ready_visible', '肩肘腕髋已入镜；先自然站稳，再按自己的舒适幅度练习。',
                (5, 6, 7, 8, 9, 10, 11, 12), {'required_joints_visible': True})


def _taichi_select(lesson_id, points, sy, hy, sx, torso, width):
    """Describe current picture-plane evidence, never certify a Tai Chi form."""
    ly, ry = points[9][1], points[10][1]
    separation = abs(points[9][0]-points[10][0])/width
    difference = abs(ly-ry)/torso
    if lesson_id in ('rooster', 'kick', 'kick_left', 'kick_right', 'snake_left', 'snake_right'):
        knees = points[13], points[14]
        if knees[0] is None or knees[1] is None:
            return _cue('taichi_leg_visibility', '膝点暂不完整，先站稳；不据此评价动作。',
                        (13, 14), kind='visibility')
        lift = abs(knees[0][1]-knees[1][1])/torso
        if lift > .14:
            return _cue('taichi_knee_height_seen',
                        '已看到两膝在画面中的高度不同；按视频缓慢收腿，不必抬高。',
                        (13, 14), {'planar_knee_height_difference_torso': round(lift, 3),
                                  'weight_bearing_observed': False})
        return _cue('taichi_leg_ready',
                    '双膝已入镜；先站稳，再按舒适幅度练习，站不稳可跳过本式。',
                    (13, 14), {'knees_visible': True, 'balance_assessed': False})
    if lesson_id == 'cloud':
        if difference > .25:
            return _cue('taichi_cloud_heights_seen',
                        '已看到双腕一高一低；对照视频缓缓交换，不必追赶示意。',
                        (9, 10), {'planar_wrist_height_difference_torso': round(difference, 3)})
        return _cue('taichi_cloud_transition',
                    '双腕在画面中高度接近；按视频慢慢划弧交换，不据此判定对错。',
                    (9, 10), {'planar_wrist_height_difference_torso': round(difference, 3)})
    if lesson_id == 'parting':
        return _cue('taichi_parting_seen' if separation > 1.35 else 'taichi_parting_ready',
                    '已看到双臂向两侧分开；缓慢收回后换侧。' if separation > 1.35 else
                    '双腕已入镜；对照视频缓慢抱合、分开，不追求深弓步。',
                    (9, 10), {'planar_wrist_separation_shoulders': round(separation, 3),
                              'stance_quality_assessed': False})
    if lesson_id == 'brush':
        return _cue('taichi_brush_heights',
                    '已看到双腕有上下位置差；按视频轻缓搂手、收回，前推深度暂不判断。' if difference > .45 else
                    '肩肘腕髋已入镜；对照视频一手下搂、一手前推，不追求幅度。',
                    (9, 10), {'planar_wrist_height_difference_torso': round(difference, 3),
                              'forward_depth_observed': False})
    if lesson_id == 'repulse':
        return _cue('taichi_repulse_scope',
                    '双臂已入镜；按视频交替回收、前推，骨架暂不判断掌向与前推深度。',
                    (7, 8, 9, 10), {'arms_visible': True, 'forward_depth_observed': False}, 'scope')
    if lesson_id in ('grasp', 'grasp_left', 'grasp_right'):
        return _cue('taichi_grasp_scope',
                    '双腕已入镜；按视频顺序缓慢完成掤、捋、挤、按，不以点位代替技术判断。',
                    (9, 10), {'wrists_visible': True, 'technique_assessed': False}, 'scope')
    if lesson_id in ('single_whip', 'single_whip_2'):
        reach = max(abs(points[9][0]-sx), abs(points[10][0]-sx))/width
        return _cue('taichi_whip_lateral_seen' if reach > 1.1 else 'taichi_whip_ready',
                    '已看到手臂向侧方舒展；勾手与弓步请以视频为准。' if reach > 1.1 else
                    '双臂已入镜；按视频轻缓向侧方舒展，不必追求幅度。',
                    (9, 10), {'planar_wrist_reach_shoulders': round(reach, 3),
                              'hook_hand_observed': False})
    if lesson_id in ('crane', 'shuttle', 'fan'):
        return _cue('taichi_upper_height_seen' if difference > .4 else 'taichi_upper_transition',
                    '已看到双腕在画面中的高低差；肩部放松，实际手臂方向以视频为准。' if difference > .4 else
                    '双腕已入镜；对照视频轻缓收放，不据此判定动作对错。',
                    (9, 10), {'planar_wrist_height_difference_torso': round(difference, 3),
                              'palm_orientation_observed': False})
    if lesson_id == 'needle':
        descent = (max(ly, ry)-hy)/torso
        return _cue('taichi_needle_descent_seen' if descent > .3 else 'taichi_needle_ready',
                    '已看到手腕在画面中下降；不以触地为目标，按舒适幅度练习。' if descent > .3 else
                    '手腕已入镜；先站稳，再对照视频缓慢向下舒展，不强行弯腰。',
                    (9, 10), {'planar_lower_wrist_below_hips_torso': round(descent, 3),
                              'lumbar_posture_assessed': False})
    if lesson_id in ('lute', 'high_pat', 'turn_punch', 'close', 'peaks'):
        return _cue('taichi_front_arms_scope',
                    '双臂已入镜；按视频缓慢收放，骨架暂不判断掌型、握拳或前推深度。',
                    (7, 8, 9, 10), {'arms_visible': True, 'forward_depth_observed': False,
                                    'hand_shape_observed': False}, 'scope')
    if lesson_id == 'cross':
        return _cue('taichi_cross_near' if separation < .60 else 'taichi_cross_ready',
                    '双腕在画面中已靠近；实际交叉与掌型请以视频为准。' if separation < .60 else
                    '双腕已入镜；由两侧缓缓收回胸前，按视频自然合抱。',
                    (9, 10), {'planar_wrist_separation_shoulders': round(separation, 3),
                              'hand_contact_observed': False})
    return _cue('taichi_finish_visible' if lesson_id == 'finish' else 'taichi_prepare_visible',
                '肩肘腕髋已入镜；双臂放松，按自己的节奏休息。' if lesson_id == 'finish' else
                '肩肘腕髋已入镜；先站稳，看完视频再按舒适幅度练习。',
                (5, 6, 7, 8, 9, 10, 11, 12), {'required_joints_visible': True})


class AdviceObserver:
    """Debounce actual frames; never keep a suggestion after its evidence ends."""
    def __init__(self, course_id='baduanjin'):
        if course_id not in ('baduanjin', 'taichi'):
            raise ValueError('Unknown coaching course')
        self.course_id = course_id
        self.reset()

    def reset(self):
        self._lesson = None
        self._last_stamp = None
        self._candidate = None
        self._since = None
        self._frames = 0
        self._last_emitted = None
        self._last_emit_stamp = None
        self._current = copy.deepcopy(EMPTY_ADVICE)

    def snapshot(self):
        return copy.deepcopy(self._current)

    def update(self, lesson_id, points, stamp, phase='practice', required=None):
        stamp = _finite(stamp)
        if phase != 'practice' or stamp is None:
            self.reset()
            return self.snapshot()
        if (lesson_id != self._lesson or self._last_stamp is not None and
                (stamp <= self._last_stamp or stamp-self._last_stamp > MAX_GAP)):
            self.reset()
        self._lesson, self._last_stamp = lesson_id, stamp
        required = tuple(required or (5, 6, 7, 8, 9, 10, 11, 12))
        candidate = _select(lesson_id, _points(points), required, self.course_id)
        code = candidate['code'] if candidate else None
        if code != self._candidate:
            self._candidate, self._since, self._frames = code, stamp, 1
            self._current = copy.deepcopy(EMPTY_ADVICE)
        else:
            self._frames += 1
        if not candidate:
            return self.snapshot()
        stable = max(0., stamp-self._since)
        if stable < candidate.pop('hold') or self._frames < 4:
            return self.snapshot()
        # A new topic is rate-limited, but an obsolete topic is already hidden.
        if (code != self._last_emitted and self._last_emit_stamp is not None
                and stamp-self._last_emit_stamp < 1.5):
            return self.snapshot()
        if code != self._last_emitted:
            self._last_emitted, self._last_emit_stamp = code, stamp
        self._current = dict(candidate, stable_seconds=round(stable, 3))
        return self.snapshot()
