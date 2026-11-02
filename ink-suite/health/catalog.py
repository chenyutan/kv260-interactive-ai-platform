"""Eight-section Baduanjin orientation and original schematic poses.

This is a software-led introductory practice aid, not an instructor-certified
motion capture course. Traditional exercise names identify the curriculum;
they are not claims that the application diagnoses, treats or prevents disease.
The schematic is original. Separate attributed local teaching videos are
listed in assets/video/manifest.json; they are not a pose-assessment standard.
"""
import math

MODES=('learn','follow','free')
MODE_LABELS={'learn':'分解学习','follow':'完整跟练','free':'自主练习'}
SOURCE_LINKS=(
    {'title':'深圳市文化广电旅游体育局：八段锦分式教学视频',
     'url':'https://wtl.sz.gov.cn/ztzl_78228/tszl/wlzgj/bdj/',
     'use':'本地非商业教学视频；保留原画面、原声及来源；不随公开源码再分发'},
    {'title':'国家体育总局：八段锦定式动作',
     'url':'https://www.sport.gov.cn/n20001280/n20001265/n20067611/c20106840/content.html',
     'use':'传统八式名称与顺序参考；未复制图片'},
    {'title':'国家体育总局：老人习练健身气功需从自身实际出发',
     'url':'https://www.sport.gov.cn/n20001280/n20001265/n20066978/c30109457/content.html',
     'use':'舒适幅度、循序渐进与不追求统一满分姿势的课程原则'},
)
REFERENCE_CAPTION='动作轮廓示意 · 非专业标准示范'
COURSE_NOTICE='按自己的舒适幅度缓慢练习，自然呼吸；不适时停止。图示仅帮助理解动作方向。'
ASSESSMENT_NOTICE='仅观察可见关节的二维变化；不判断掌型、呼吸、头颈转向、脊柱姿态或真实重心。'
POSE_EDGES=((5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16))

def lesson(identifier,name,short,instructions,observable,assessment,cycle=12.,cycles=3,required=(5,6,7,8,9,10,11,12)):
    return dict(id=identifier,name=name,title=name,short_name=short,
                instructions=tuple(instructions),instruction=instructions[0],
                observable=observable,assessment_note=assessment,
                cycle_seconds=float(cycle),cycles=cycles,demo_seconds=float(cycle),
                practice_seconds=float(cycle)*cycles,required_joints=tuple(required))

COURSE=(
    lesson('prepare','预备式','预备',
           ('自然站立，双脚放稳，肩臂放松。','调整位置，让肩、肘、腕、髋和所需腿部关节入镜。','按舒适范围练习，不必追求图示幅度。'),
           False,'预备阶段只检查入镜，不评动作标准。',cycle=10.,cycles=1),
    lesson('raise','两手托天理三焦','两手托天',
           ('双手从身前缓慢上举，抬到舒适高度。','双臂向上舒展，肩部不刻意耸起。','沿身体两侧缓缓放下，回到自然站立。'),
           True,'可记录双腕上举后放下的二维往返；不判断掌心与手指动作。'),
    lesson('bow','左右开弓似射雕','左右开弓',
           ('双手抬至胸前，一侧手臂向侧方舒展。','另一侧手臂弯曲回收，动作保持平缓。','收回后换另一侧，站距与屈膝幅度以舒适为准。'),
           True,'可记录侧方展开与收回；手型、视线和下肢技术不在本轮评价内。',cycle=16.),
    lesson('single','调理脾胃须单举','交替单举',
           ('一手缓缓上举，另一手在体侧向下舒展。','在舒适范围内伸展，身体保持平稳。','双手收回身前，再交换上下方向。'),
           True,'可记录交替上举与回收；不以图示高度要求所有人。',cycle=16.),
    lesson('look','五劳七伤往后瞧','缓慢回望',
           ('保持自然站姿，肩臂放松。','只在舒适范围内缓慢向一侧回望。','回到正前方，再换另一侧；不要勉强转动。'),
           False,'当前骨架没有可靠头颈朝向信息，本式只提供跟练，不作转头评分。',cycle=16.),
    lesson('sway','摇头摆尾去心火','缓慢转移',
           ('双脚放稳，双手轻放大腿，身体轻微下沉。','用较小幅度缓慢移动躯干，再回到中间。','换另一侧完成；不追求深蹲、大幅摆动或极限转腰。'),
           True,'只记录躯干在图像平面内的左右往返；不推断真实重心、头颈与脊柱动作。',cycle=18.,required=(5,6,7,8,9,10,11,12,13,14)),
    lesson('bend','两手攀足固肾腰','缓慢俯身',
           ('双手随身体缓缓向下，按舒适范围轻微俯身。','不以手碰到脚为目标，不强行弯腰。','缓慢起身回到自然站立；不适时跳过本式。'),
           True,'仅记录双腕下降后回升；不评价腰椎形态或要求触足。',cycle=18.,required=(5,6,7,8,9,10,11,12,13,14)),
    lesson('fist','攒拳怒目增气力','交替伸臂',
           ('双手在体侧收回，采用舒适站姿。','一侧手臂缓缓向前伸出，再收回。','换另一侧；不要求用力冲拳，不刻意憋气。'),
           False,'单个正面摄像头无法可靠判断前推深度和握拳，本式只跟练，不作冲拳次数评分。',cycle=16.),
    lesson('heel','背后七颠百病消','轻缓提踵',
           ('站稳后，在可保持平衡的范围内轻缓提踵。','平稳落下，动作轻柔，不追求震动。','不方便提踵时保持站立或跳过，不强行完成。'),
           False,'当前模型没有脚跟、脚尖点，本式不以踝点浮动推断提踵或落地次数。',cycle=12.),
    lesson('finish','收势','收势',
           ('双手在身前缓缓收回，双臂自然放松。','身体恢复自然站立，按自己的节奏休息。','本次记录仅为练习过程，不代表健康诊断。'),
           False,'记录课程结束与练习时长，不生成健康分数。',cycle=10.,cycles=1),
)
LESSONS=COURSE
BY_ID={item['id']:item for item in COURSE}

def _neutral():
    return [(None if i<5 else [0.,0.,1.]) for i in range(17)]

NEUTRAL={0:(.50,.17),5:(.42,.30),6:(.58,.30),7:(.37,.43),8:(.63,.43),9:(.36,.56),10:(.64,.56),11:(.44,.57),12:(.56,.57),13:(.43,.74),14:(.57,.74),15:(.42,.92),16:(.58,.92)}

def _target(identifier,side=1):
    pose=dict(NEUTRAL)
    if identifier=='raise':
        pose.update({7:(.35,.16),8:(.65,.16),9:(.47,.065),10:(.53,.065)})
    elif identifier=='bow':
        pose.update({7:(.28,.31),9:(.14,.31),8:(.70,.34),10:(.55,.33)})
    elif identifier=='single':
        pose.update({7:(.37,.16),9:(.43,.065),8:(.63,.43),10:(.65,.60)})
    elif identifier=='look':
        # This is a direction marker for the head, never an observed score.
        pose[0]=(.455,.17)
        pose.update({9:(.32,.56),10:(.68,.56)})
    elif identifier=='sway':
        pose.update({0:(.43,.25),5:(.33,.37),6:(.49,.36),7:(.36,.48),8:(.62,.46),9:(.36,.62),10:(.64,.62),11:(.44,.60),12:(.56,.60),13:(.38,.75),14:(.62,.75),15:(.32,.92),16:(.68,.92)})
    elif identifier=='bend':
        pose.update({0:(.50,.47),5:(.42,.50),6:(.58,.50),7:(.40,.65),8:(.60,.65),9:(.40,.80),10:(.60,.80)})
    elif identifier=='fist':
        # Projected forward arm is foreshortened; caption explains that depth is
        # not represented or evaluated by a frontal two-dimensional diagram.
        pose.update({7:(.38,.36),9:(.41,.35),8:(.65,.43),10:(.61,.51)})
    elif identifier=='heel':
        for i,(x,y) in tuple(pose.items()):
            if i not in (15,16):pose[i]=(x,y-.025)
        pose[15]=(.42,.90);pose[16]=(.58,.90)
    elif identifier in ('prepare','finish'):
        pose.update({7:(.40,.43),8:(.60,.43),9:(.47,.49),10:(.53,.49)})
    if side<0:
        # Mirror the displayed direction while preserving COCO joint names.
        for a,b in ((5,6),(7,8),(9,10),(11,12),(13,14),(15,16)):
            pa,pb=pose[a],pose[b]
            pose[a]=(1-pb[0],pb[1]);pose[b]=(1-pa[0],pa[1])
        pose[0]=(1-pose[0][0],pose[0][1])
    return pose

def reference_pose(identifier,phase=0.):
    """Return an original smoothly animated 17-slot 2D direction schematic."""
    if isinstance(identifier,int):identifier=COURSE[identifier]['id']
    if identifier not in BY_ID:raise ValueError('Unknown lesson')
    phase=float(phase)
    if not math.isfinite(phase):phase=0.
    phase=max(0.,min(1.,phase))
    bilateral=identifier in ('bow','single','look','sway','fist')
    local=(phase*2)%1. if bilateral and phase<1 else phase
    if bilateral and phase==1:local=0.
    side=-1 if bilateral and .5<=phase<1 else 1
    # neutral -> movement -> neutral; no snapping at the cycle boundary.
    blend=math.sin(math.pi*local)**2
    target=_target(identifier,side)
    base=dict(NEUTRAL)
    if identifier=='bow':
        base.update({7:(.35,.42),8:(.65,.42),9:(.47,.36),10:(.53,.36)})
    elif identifier=='sway':
        # Once this exercise starts the feet stay planted; only the indicated
        # upper-body shift changes, rather than repeatedly stepping wide.
        base.update({0:(.50,.20),5:(.42,.33),6:(.58,.33),7:(.36,.48),8:(.64,.48),9:(.36,.62),10:(.64,.62),11:(.44,.60),12:(.56,.60),13:(.38,.75),14:(.62,.75),15:(.32,.92),16:(.68,.92)})
        for index in (11,12,13,14,15,16):target[index]=base[index]
    out=[]
    for i in range(17):
        if i not in NEUTRAL:out.append(None);continue
        a=base[i];b=target[i]
        out.append([a[0]+(b[0]-a[0])*blend,a[1]+(b[1]-a[1])*blend,1.])
    return out
