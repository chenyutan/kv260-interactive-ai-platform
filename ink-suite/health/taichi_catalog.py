"""Complete simplified 24-form Tai Chi with original 2D direction schematics.

Names/order follow primary university teaching materials for all 24 forms.
The schematic is not captured from, or validated against, a professional teacher.
It does not establish palm orientation, weight bearing or three-dimensional form.
"""
import math

from .catalog import NEUTRAL, POSE_EDGES, lesson

SOURCE_LINKS = (
    {'title': '北京理工大学：二十四式太极拳视频教程',
     'url': 'https://opt.bit.edu.cn/zlxz/b58242.htm',
     'use': '官网公开下载的真人教程用于本地学习；媒体来源、分式边界与处理清单单独保留，不随公开源码再分发'},
    {'title': '福州大学：二十四式太极拳动作及简化口诀',
     'url': 'https://jgxy.fzu.edu.cn/info/1116/4839.htm',
     'use': '完整二十四式名称、顺序和分解动作方向参考；文字说明为本项目改写'},
    {'title': '国家高等教育智慧教育平台：二十四式太极拳教学',
     'url': 'https://higher.smartedu.cn/course/641e248daf1f1b5d3ecfff1b',
     'use': '二十四式顺序交叉核对；本地视频的实际来源另列于媒体清单'},
    {'title': '国家体育总局：太极拳普及套路介绍',
     'url': 'https://www.sport.gov.cn/n20001280/n20745751/n20767239/c21618661/content.html',
     'use': '简化二十四式课程范围参考；不复制健康功效承诺'},
)
REFERENCE_CAPTION = '动作方向示意 · 真人示范以视频为准'
COURSE_NOTICE = '先看真人示范，再按舒适幅度慢练；下势不追求深蹲，抬腿不追求高度，站不稳时可跳过。'
ASSESSMENT_NOTICE = '仅提示可见关节的二维位置；不评掌型、劲力、呼吸、真实重心或套路完成质量，不自动计完整动作次数。'
UPPER = (5, 6, 7, 8, 9, 10, 11, 12)
FULL = tuple(range(5, 17))
BILATERAL = frozenset(('parting', 'brush', 'repulse', 'cloud', 'shuttle'))


def _lesson(identifier, name, instructions, note, cycle=20., cycles=1, required=UPPER):
    row = lesson(identifier, name, name, instructions, False, note,
                 cycle=cycle, cycles=cycles, required=required)
    row['bilateral'] = identifier in BILATERAL
    row['count_repetitions'] = False
    return row


COURSE = (
    _lesson('prepare', '起势',
            ('自然站立，双脚放稳，肩臂放松。', '双臂从身前缓缓举起，再轻轻放下。', '先看视频，活动范围以舒适为准。'),
            '只检查入镜和双腕位置，不评价屈膝深度或呼吸。', 12.0, required=UPPER),
    _lesson('parting', '左右野马分鬃',
            ('双手在身前作上下抱合，先看清视频方向。', '小步舒展，双臂缓缓向不同方向分开。', '收回抱合后换另一侧，不追求深弓步。'),
            '可提示画面中的手臂分开与回收；不判断掌型、弓步质量和发力。', 24.0, required=FULL),
    _lesson('crane', '白鹤亮翅',
            ('按视频缓缓跟步，双手轻柔收合。', '一臂向上、一臂向下分开，肩部保持放松。', '停在自己舒适的位置，不刻意耸肩或后仰。'),
            '仅提示双腕在画面中的高低差；不判虚步承重、掌向或脊柱姿态。', 20.0, required=UPPER),
    _lesson('brush', '左右搂膝拗步',
            ('按视频缓缓摆手，迈步幅度不要过大。', '一手向下搂过，另一手缓缓前推。', '收回后换侧，保持自己能稳定完成的姿态。'),
            '只观察上下肢是否入镜和双腕上下位置；不判断真实重心或膝关节受力。', 24.0, required=FULL),
    _lesson('lute', '手挥琵琶',
            ('按视频跟步并轻缓收手。', '双臂在身前自然弯曲，一手稍高、一手稍低。', '肩肘放松，不用力夹臂，站稳后再继续。'),
            '只提示双腕在身前的位置；不能确认虚步承重或手掌接触。', 20.0, required=UPPER),
    _lesson('repulse', '左右倒卷肱',
            ('一手缓缓后展，另一手在身前放松。', '屈臂回收，再按视频慢慢前推。', '收回后换另一侧，动作连贯，不用力冲掌。'),
            '可提示双腕高度；正面二维骨架不能确认前推深度、转腰与掌向。', 24.0, required=UPPER),
    _lesson('grasp_left', '左揽雀尾',
            ('先看视频中左侧掤、捋、挤、按的顺序。', '双臂在身前缓慢运行，步幅和屈膝保持舒适。', '按视频轻缓衔接，不强行扭腰或推压。'),
            '可提示双腕在身前的位置；不以二维点位确认掤捋挤按技术或完整次数。', 28.0, required=FULL),
    _lesson('grasp_right', '右揽雀尾',
            ('按视频转向右侧，先站稳再运行双臂。', '依次缓慢完成掤、捋、挤、按。', '步幅以舒适为准，衔接时不要急转。'),
            '可提示双腕入镜和身前位置；不能确认真实重心、掌型与前后推掌深度。', 28.0, required=FULL),
    _lesson('single_whip', '单鞭',
            ('按视频缓慢转身运臂，先看清方向。', '一手向侧方舒展，另一手依示范自然收合。', '轻缓上步，肩部放松，不追求大幅弓步。'),
            '仅提示画面中的侧向伸臂；不判断勾手、掌向和弓步技术。', 20.0, required=FULL),
    _lesson('cloud', '云手',
            ('双臂在身前放松弯曲，一手高、一手低。', '按视频方向缓缓划弧，左右交换。', '小步移动或原地练习，不追赶示意的节奏。'),
            '只观察双腕高低和入镜状态；不由躯干侧移推断重心转移。', 24.0, required=UPPER),
    _lesson('single_whip_2', '单鞭',
            ('接云手后，按视频缓缓转身收脚。', '轻缓上步并向侧方舒展手臂。', '肩肘放松，保持能稳定完成的步幅。'),
            '本式是二十四式中的第二个单鞭；仍只提示二维侧向伸臂，不判手型。', 20.0, required=FULL),
    _lesson('high_pat', '高探马',
            ('按视频轻缓跟步，双手自然收合。', '一臂回收，另一手慢慢向身前推出。', '保持肩部放松，不用力推压。'),
            '双腕位置可见，但前推深度与虚步承重不由正面骨架推断。', 20.0, required=UPPER),
    _lesson('kick_right', '右蹬脚',
            ('先站稳，按视频缓缓提右膝。', '在舒适幅度内慢慢伸右腿，双臂轻缓展开。', '屈膝收腿、轻轻落脚；不追求踢高，站不稳可跳过。'),
            '仅观察膝与踝是否入镜；不判断足尖方向、支撑脚承重或蹬脚力度。', 20.0, required=FULL),
    _lesson('peaks', '双峰贯耳',
            ('按视频缓缓收腿、落手，先站稳。', '双臂从身侧向身前轻缓合拢。', '保持柔和，不用力击打或憋气。'),
            '只提示双腕相互靠近；不能确认握拳、实际接触或发力。', 20.0, required=UPPER),
    _lesson('kick_left', '转身左蹬脚',
            ('先看视频的转身方向，缓慢转身站稳。', '轻缓提左膝，再按舒适幅度慢慢伸左腿。', '收腿、落脚，不追求高度或急转；不方便时可跳过。'),
            '仅观察膝踝入镜与二维位置；不评价转身角度、蹬脚力度和支撑脚承重。', 20.0, required=FULL),
    _lesson('snake_left', '左下势独立',
            ('先看完整示范，下沉只取自己的舒适幅度。', '按视频轻缓伸臂、起身，再小幅提膝。', '不追求低仆步或高抬腿；站不稳时可跳过。'),
            '只提示可见膝点高度与关节入镜；不据此判定仆步、承重、平衡或完整动作。', 26.0, required=FULL),
    _lesson('snake_right', '右下势独立',
            ('按视频换到另一侧，先站稳再轻缓下沉。', '在舒适范围内伸臂、起身，再小幅提膝。', '不追求下沉深度；不方便单脚站立时可跳过。'),
            '只提示可见膝点高度与入镜；不评价真实重心、腿部受力或平衡能力。', 26.0, required=FULL),
    _lesson('shuttle', '左右穿梭',
            ('按视频缓慢收脚抱手，先看清迈步方向。', '轻缓上步，一手上架，另一手向身前舒展。', '收回后换侧，步幅保持舒适，不强行转腰。'),
            '只观察双腕高低与关节入镜；不判断架掌方向、前推深度或弓步承重。', 24.0, required=FULL),
    _lesson('needle', '海底针',
            ('按视频轻缓跟步，先把身体站稳。', '一手随身缓缓向下，俯身只到舒适位置。', '不以手触地为目标，不强行弯腰。'),
            '仅描述手腕在图像中下降；不评价腰椎姿态、虚步承重或触地程度。', 20.0, required=FULL),
    _lesson('fan', '闪通臂',
            ('按视频缓缓起身，收脚提手。', '轻缓上步，一臂向上舒展，另一手向身前舒展。', '肩部放松，不追求伸直或极限幅度。'),
            '可提示双腕在画面中的高低位置；不推断前推深度、掌向或发力。', 20.0, required=UPPER),
    _lesson('turn_punch', '转身搬拦捶',
            ('先看视频的转身、搬、拦、捶顺序。', '缓慢转身、上步，手臂依示范收放。', '动作轻柔，不要求用力冲拳或快速转身。'),
            '只检查可见上肢关节；不判断拳型、转身深度和打击力量。', 26.0, required=FULL),
    _lesson('close', '如封似闭',
            ('按视频缓缓引手回收，肩肘放松。', '双手向身前轻缓舒展，不用力推压。', '自然衔接下一式，步幅和屈膝以舒适为准。'),
            '只提示双腕可见位置；正面二维骨架不能确认前后按掌深度和真实重心。', 20.0, required=UPPER),
    _lesson('cross', '十字手',
            ('双臂由两侧缓缓收回身前。', '按视频在胸前交叉合抱，肩部保持放松。', '自然站稳，准备收势。'),
            '只提示双腕在图像平面内接近；不能据此证明双手实际交叉或接触。', 14.0, required=UPPER),
    _lesson('finish', '收势',
            ('双手在身前缓缓分开并放下。', '双臂自然垂落，恢复舒适站姿。', '结束后按自己的节奏休息。'),
            '只记录跟练过程和时长，不生成健康分数。', 12.0, required=UPPER),
)
for _number, _row in enumerate(COURSE, 1):
    _row['sequence_number'] = _number
LESSONS = COURSE
BY_ID = {item['id']: item for item in COURSE}


def _mirror(pose):
    output = dict(pose)
    for left, right in ((5,6),(7,8),(9,10),(11,12),(13,14),(15,16)):
        a, b = pose[left], pose[right]
        output[left] = (1-b[0], b[1])
        output[right] = (1-a[0], a[1])
    output[0] = (1-pose[0][0], pose[0][1])
    return output


def _target(identifier, side=1):
    pose = dict(NEUTRAL)
    poses = {
        'prepare': {7:(.34,.34),8:(.66,.34),9:(.33,.30),10:(.67,.30)},
        'parting': {7:(.30,.34),9:(.18,.27),8:(.63,.43),10:(.72,.56)},
        'crane': {7:(.34,.29),9:(.43,.15),8:(.66,.44),10:(.69,.59)},
        'brush': {7:(.35,.47),9:(.33,.61),8:(.67,.32),10:(.71,.30)},
        'lute': {7:(.36,.38),9:(.49,.31),8:(.63,.43),10:(.51,.44)},
        'repulse': {7:(.28,.34),9:(.21,.26),8:(.63,.39),10:(.51,.34)},
        'grasp_left': {7:(.31,.38),9:(.40,.31),8:(.67,.39),10:(.55,.33)},
        'single_whip': {7:(.28,.31),9:(.15,.29),8:(.70,.35),10:(.80,.34)},
        'cloud': {7:(.33,.34),9:(.48,.26),8:(.66,.44),10:(.53,.50)},
        'high_pat': {7:(.33,.39),9:(.44,.43),8:(.65,.33),10:(.57,.29)},
        'kick_right': {7:(.29,.32),9:(.20,.29),8:(.71,.32),10:(.80,.29),
                       14:(.66,.62),16:(.82,.63)},
        'peaks': {7:(.31,.36),9:(.45,.31),8:(.69,.36),10:(.55,.31)},
        'snake_left': {7:(.30,.42),9:(.21,.51),8:(.67,.33),10:(.73,.30),
                       13:(.41,.61),15:(.43,.76)},
        'shuttle': {7:(.33,.23),9:(.47,.16),8:(.68,.34),10:(.74,.30)},
        'needle': {0:(.49,.24),5:(.41,.35),6:(.57,.35),7:(.39,.49),9:(.44,.72),8:(.61,.46),10:(.57,.55)},
        'fan': {7:(.35,.23),9:(.39,.12),8:(.68,.33),10:(.78,.32)},
        'turn_punch': {7:(.33,.38),9:(.44,.42),8:(.67,.37),10:(.59,.36)},
        'close': {7:(.34,.37),9:(.43,.32),8:(.66,.37),10:(.57,.32)},
        'cross': {7:(.32,.43),9:(.53,.35),8:(.68,.43),10:(.47,.35)},
        'finish': {7:(.39,.43),9:(.44,.53),8:(.61,.43),10:(.56,.53)},
    }
    aliases = {'grasp_right':'grasp_left', 'single_whip_2':'single_whip',
               'kick_left':'kick_right', 'snake_right':'snake_left'}
    pose.update(poses[aliases.get(identifier, identifier)])
    if identifier in ('grasp_right', 'kick_left', 'snake_right'):
        pose = _mirror(pose)
    if side < 0:
        pose = _mirror(pose)
    return pose


def reference_pose(identifier, phase=0.):
    """Original smooth direction outline, never an assessed ideal pose."""
    if isinstance(identifier, int) and not isinstance(identifier, bool):
        identifier = COURSE[identifier]['id']
    if identifier not in BY_ID:
        raise ValueError('Unknown Tai Chi lesson')
    phase = float(phase)
    if not math.isfinite(phase):
        phase = 0.
    phase = max(0., min(1., phase))
    bilateral = identifier in BILATERAL
    local = (phase * 2) % 1. if bilateral else phase
    side = -1 if bilateral and .5 <= phase < 1. else 1
    blend = math.sin(math.pi * local) ** 2
    if phase in (0., 1.) or bilateral and phase == .5:
        blend = 0.
    target = _target(identifier, side)
    return [None if index not in NEUTRAL else
            [NEUTRAL[index][0] + (target[index][0]-NEUTRAL[index][0])*blend,
             NEUTRAL[index][1] + (target[index][1]-NEUTRAL[index][1])*blend, 1.]
            for index in range(17)]
