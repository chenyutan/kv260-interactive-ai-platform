"""Three-panel practice presentation, without changing lesson or pose truth.

Logical coordinates use the host's existing 960 x 540 transform.  The host owns
Pango's bounded text cache, video decoding, input and all report operations.
This module draws the latest supplied frame, a separate reference illustration,
and observed user joints. Reference samples never enter observation or counting.
"""
import math
import time
from .reference_demo import fallback_pose, project_reference_pose, EDGES

INK = (.105, .225, .218)
GREEN = (.16, .35, .32)
MUTED = (.43, .48, .45)
GOLD = (.64, .49, .28)
CINNABAR = (.66, .26, .20)
PAPER = (.978, .969, .948)
MODE_NAMES = {'learn': '分解学习', 'follow': '完整跟练', 'free': '自主练习'}
FREE_SUMMARIES = {
    'raise': '缓慢上举 → 舒适伸展 → 沿体侧放下，按自己的节奏完成。',
    'bow': '胸前抬手 → 向一侧开弓 → 缓慢收回，再换另一侧。',
    'single': '一手上举、另一手下按，缓慢收回后交换方向。',
    'look': '站稳放松，在舒适范围内缓慢回望，再回到正前方。',
    'sway': '放稳双脚，小幅移动躯干后回中，再换另一侧。',
    'bend': '按舒适幅度缓慢俯身，再慢慢起身；不必触足。',
    'fist': '一侧手臂缓慢前伸、收回，再换另一侧；不用猛力。',
    'heel': '站稳后轻缓提踵，再平稳落下；不方便时可跳过。',
}

REFERENCE_PANEL = (34, 119, 434, 410)
REFERENCE_FRAME = (46, 157, 422, 369)
DEMO_PANEL = (446, 119, 676, 410)
DEMO_BODY = (456, 157, 666, 365)
OBSERVED_PANEL = (688, 119, 926, 410)
OBSERVED_BODY = (698, 157, 916, 347)
INSTRUCTION_BOX = (34, 417, 926, 450)


def _number(value, default=0):
    try:
        if isinstance(value, bool):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def guide_identity(state, course=None):
    """Keep preparation, exercise numbering and free-mode progression explicit."""
    state = state if isinstance(state, dict) else {}
    course = course if isinstance(course, dict) else {}
    lessons = course.get('lessons') or ()
    course_id = state.get('course_id') or course.get('id') or 'baduanjin'
    count = max(1, int(_number(state.get('lesson_count'), len(lessons) or 10)))
    index = max(0, min(count-1, int(_number(state.get('lesson_index')))))
    lesson = lessons[index] if index < len(lessons) else {}
    identifier = state.get('lesson_id') or lesson.get('id')
    name = str(state.get('lesson_name') or lesson.get('name') or '当前动作')[:96]
    preparation = course_id == 'baduanjin' and identifier == 'prepare'
    finishing = course_id == 'baduanjin' and identifier == 'finish'
    if preparation:
        title, ordinal = '当前动作 · 预备式', '预备式'
    elif finishing:
        title, ordinal = '当前动作 · 收势', '收势'
    else:
        number = index+1 if course_id == 'taichi' else max(1, index)
        ordinal = '第 {} 式'.format(number)
        title = '{} · {}'.format(ordinal, name)
    phase = {'demo': '先看示意', 'practice': '跟练中',
             'paused': '已暂停', 'completed': '已结束'}.get(state.get('phase'), '准备中')
    mode = state.get('mode')
    if mode == 'free' and preparation:
        subtitle = '自主练习 · 当前为预备式，准备好后点“开始第一式”'
        instruction = '准备好后，选择下方“开始第一式”，进入两手托天。'
    elif mode == 'free' and index == count-1:
        subtitle = '自主练习 · 当前是最后一式，完成后选择“返回健康”'
        instruction = '本式完成后选择“返回健康”，练习记录会自动保存。'
    elif mode == 'free':
        subtitle = '自主练习 · 本页不会自动换式，练好后选择“下一式”'
        # A free-practice clock cannot tell which movement the user is doing.
        # Keep a stable whole-exercise summary; actual corrections below come
        # only from the existing observed-advice result.
        instruction = (FREE_SUMMARIES.get(identifier) if course_id == 'baduanjin' else None)
        instruction = instruction or '跟随左侧真人示范，按自己的节奏完成本式，练好后选择“下一式”。'
    else:
        subtitle = '{} · 示范节奏 · 可随时暂停或选择动作'.format(MODE_NAMES.get(mode, '跟练'))
        instruction = '示范要点：'+str(state.get('instruction') or '先看清真人示范，再按舒适幅度练习。')
    return dict(title=title, ordinal=ordinal, subtitle=subtitle,
                preparation=preparation, finishing=finishing, phase=phase,
                instruction=instruction[:280], lesson_id=identifier,
                course_id=course_id, index=index, count=count)


def guide_button_label(identifier, label, state):
    """A clearer label keeps the existing next-action id and controller target."""
    if identifier == 'health_next' and guide_identity(state)['preparation']:
        return '开始第一式'
    return label


def fit_frame(width, height, box=REFERENCE_FRAME):
    """Fit the complete reference frame; never crop a teacher's head or feet."""
    width, height = _number(width), _number(height)
    if width <= 0 or height <= 0:
        return None
    a, b, c, d = box
    scale = min((c-a)/width, (d-b)/height)
    return a+(c-a-width*scale)/2., b+(d-b-height*scale)/2., scale


def observed_points(points):
    """Filter actual observations only; missing joints remain missing."""
    result = []
    for index in range(17):
        try:
            raw = points[index]
            if any(isinstance(value, bool) for value in raw[:3]):
                raise ValueError('Boolean coordinate')
            x, y, confidence = map(float, raw[:3])
            valid = (all(math.isfinite(v) for v in (x, y, confidence))
                     and 0 <= x <= 1 and 0 <= y <= 1 and .08 <= confidence <= 1)
            result.append((x, y, confidence) if valid else None)
        except (TypeError, ValueError, IndexError, KeyError):
            result.append(None)
    return tuple(result)


def save_caption(status, enabled=True):
    if not enabled:
        return '演示模式 · 不保存记录', MUTED
    status = status if isinstance(status, dict) else {}
    state = status.get('state')
    if state in ('error', 'failed', 'unavailable') or status.get('error'):
        return '记录待重试', CINNABAR
    if state in ('saving', 'pending', 'queued'):
        return '正在自动保存', MUTED
    if state in ('saved', 'idle_saved'):
        return '已自动保存', GREEN
    return '练习记录自动保存', MUTED


def _arrow(cr, x, y, dx, dy):
    length=math.hypot(dx,dy)
    if length<1:return
    ux,uy=dx/length,dy/length
    cr.set_source_rgba(*GOLD,.9);cr.set_line_width(2.)
    cr.move_to(x,y);cr.line_to(x+dx,y+dy);cr.stroke()
    cr.move_to(x+dx-ux*6-uy*3,y+dy-uy*6+ux*3)
    cr.line_to(x+dx,y+dy)
    cr.line_to(x+dx-ux*6+uy*3,y+dy-uy*6-ux*3);cr.stroke()


def draw_reference(cr, pose, box=DEMO_BODY):
    """Reference-only complete teaching figure; never enters the user renderer."""
    projected=project_reference_pose(pose,box)
    a,b,c,d=box;cr.save();cr.rectangle(a,b,c-a,d-b);cr.clip();cr.set_line_cap(1)
    if all(projected[i] is not None for i in (5,6,11,12)):
        cr.set_source_rgba(*GREEN,.10);cr.move_to(*projected[5][:2])
        for i in (6,12,11):cr.line_to(*projected[i][:2])
        cr.close_path();cr.fill()
    # An illustrated head/neck is reference artwork, not a body-model claim.
    if projected[5] is not None and projected[6] is not None:
        l,r=projected[5],projected[6];sx,sy=(l[0]+r[0])/2,(l[1]+r[1])/2
        radius=max(6.,min(11.,math.hypot(l[0]-r[0],l[1]-r[1])*.23))
        head=projected[0];hx,hy=head[:2] if head is not None else (sx,sy-radius*1.9)
        cr.set_source_rgba(*GREEN,.8);cr.set_line_width(3.)
        cr.move_to(sx,sy);cr.line_to(hx,hy+radius*.7);cr.stroke()
        cr.set_source_rgb(*PAPER);cr.arc(hx,hy,radius,0,math.tau);cr.fill_preserve()
        cr.set_source_rgb(*GREEN);cr.set_line_width(2.);cr.stroke()
    for i,j in EDGES:
        p,q=projected[i],projected[j]
        if p is None or q is None:continue
        cr.set_source_rgba(*GREEN,.12);cr.set_line_width(10.)
        cr.move_to(*p[:2]);cr.line_to(*q[:2]);cr.stroke()
        cr.set_source_rgba(*GREEN,.92);cr.set_line_width(4.5)
        cr.move_to(*p[:2]);cr.line_to(*q[:2]);cr.stroke()
    for i,p in enumerate(projected[5:17],5):
        if p is None:continue
        cr.set_source_rgb(*PAPER);cr.arc(*p[:2],3.6,0,math.tau);cr.fill_preserve()
        cr.set_source_rgb(*GREEN);cr.set_line_width(1.4);cr.stroke()
        if i in pose.get('active_joints',()):
            cr.set_source_rgb(*GOLD);cr.set_line_width(1.8);cr.arc(*p[:2],6.,0,math.tau);cr.stroke()
    kind=pose.get('arrow_kind')
    for i in (9,10):
        p=projected[i]
        if p is None:continue
        side=-1 if p[0]<(a+c)/2 else 1
        if kind=='up':_arrow(cr,p[0]+side*12,p[1]+12,0,-23)
        elif kind=='out_down':_arrow(cr,p[0]+side*10,p[1]-12,side*7,22)
    if kind=='forward':
        cr.set_source_rgb(*GOLD);cr.set_line_width(1.8)
        cr.arc(c-14,b+17,7,0,math.tau);cr.stroke();cr.arc(c-14,b+17,2.2,0,math.tau);cr.fill()
    cr.restore()
    return projected


def draw_practice(view, cr, state=None, *, now=None, save_status=None):
    """Draw through existing cached host primitives; performs no I/O or writes.

    Required host attributes: current, course, video_enabled, video_player,
    pose_received, _course_header/_badge/_text/_panel/_body/_buttons.
    ``save_status`` is an acknowledged autosave status, not a report mutation.
    """
    state = view.current if state is None else state
    identity = guide_identity(state, getattr(view, 'course', None))
    view._course_header(cr, identity['title'], identity['subtitle'])
    phase_color = CINNABAR if state.get('status') == 'paused' else GREEN
    view._badge(cr, identity['phase'], 654, 33, 114, color=phase_color)
    elapsed = max(0, int(_number(state.get('session_elapsed'))))
    view._text(cr, '{:02}:{:02}'.format(elapsed//60, elapsed%60), 796, 30, 26,
               color=GREEN, bold=True, width=128, centered=True)
    caption, color = save_caption(save_status, getattr(view, 'save_reports', True))
    view._text(cr, caption, 778, 72, 12, color=color, width=146, centered=True, height=20)
    for box in (REFERENCE_PANEL,DEMO_PANEL,OBSERVED_PANEL):view._panel(cr,box)
    view._text(cr, '真人示范', 50, 130, 16, bold=True, width=370, height=24)
    view._text(cr, '示范骨架', 458, 130, 16, bold=True, width=204, height=24)
    view._text(cr, '我的动作', 700, 130, 16, bold=True, width=118, height=24)
    if identity['course_id']=='baduanjin' and identity['lesson_id'] in ('raise','bow','single','sway','bend'):
        count=max(0,int(_number(state.get('repetition'))))
        view._text(cr,'{} 次'.format(count),844,133,13,color=GREEN,bold=True,width=68,centered=True,height=20)
    companion = state.get('companion_video') or {}
    packet=None;frame=None
    if companion.get('clip') and not companion.get('error_message'):
        packet_fn=getattr(view.video_player,'frame_packet',None)
        if callable(packet_fn):
            packet=packet_fn()
            if packet and packet.get('surface') is not None:
                frame=(packet['surface'],packet['width'],packet['height'])
        else:frame=view.video_player.frame()
    fit = fit_frame(frame[1], frame[2]) if frame and len(frame) == 3 else None
    if fit:
        x, y, scale = fit;a,b,c,d = REFERENCE_FRAME
        cr.save();cr.rectangle(a,b,c-a,d-b);cr.clip()
        cr.set_source_rgb(.055,.08,.075);cr.paint()
        cr.translate(x,y);cr.scale(scale,scale)
        cr.set_source_surface(frame[0],0,0);cr.paint();cr.restore()
        footnote = '真人参考 · 静音循环 · 回看可放大'
    else:
        message = ('示范未启用' if not view.video_enabled else '示范暂不可用'
                   if companion.get('error_message') else '正在准备真人示范…')
        view._text(cr, message, 52, 240, 15, color=MUTED, width=364, centered=True)
        footnote = '回看视频可重试 · 仍可参考右侧图示'
    view._text(cr, footnote, 46, 389, 11, color=MUTED, width=376, centered=True, height=16)
    library=getattr(view,'reference_library',None);reference=None
    if fit and packet and library is not None:
        expected_generation=companion.get('generation',companion.get('epoch'))
        if expected_generation is None or packet.get('generation')==expected_generation:
            reference=library.sample(packet.get('pts_seconds'),
                                     clip_path=(companion.get('clip') or {}).get('local_path'),
                                     generation=packet.get('generation'))
    if reference is None:
        reference=fallback_pose(identity['course_id'],identity['lesson_id'],state.get('cycle_progress',0.))
    projected=draw_reference(cr,reference)
    if projected[5] is not None and projected[6] is not None:
        left_label,right_label=('左侧','右侧') if projected[5][0]<projected[6][0] else ('右侧','左侧')
        view._text(cr,left_label,460,155,9,color=MUTED,width=40,height=14)
        view._text(cr,right_label,622,155,9,color=MUTED,width=40,centered=True,height=14)
    view._text(cr,'示范：'+reference['phase_label'],456,370,12,color=GREEN,bold=True,width=210,centered=True,height=17)
    view._text(cr,reference['caption'],454,391,10,color=MUTED,width=214,centered=True,height=14)
    if reference.get('arrow_kind')=='forward':
        view._text(cr,'向身前 · 方向示意',462,345,10,color=GOLD,width=198,centered=True,height=15)
    now = time.monotonic() if now is None else now
    received = getattr(view, 'pose_received', None)
    fresh = received is not None and 0 <= now-received <= .65
    actual = state.get('display_pose_points', state.get('pose_points')) or ()
    points = observed_points(actual) if fresh else (None,)*17
    anchor_points = points
    held = ()
    stabilizer = getattr(view,'display_filter',None)
    packet = stabilizer.sample(now=now) if fresh and stabilizer is not None else None
    if packet is not None:
        points = packet['points']
        held = tuple(i for i,flag in enumerate(packet['held']) if flag)
        anchor_points = packet['raw_points']
    visible = sum(p is not None and i not in held for i,p in enumerate(points) if 5<=i<17)
    weak = sum(p is not None and p[2] < .15 and i not in held for i,p in enumerate(points) if 5<=i<17)
    if visible or held:
        view._body(cr, points, OBSERVED_BODY, held=held, anchor_points=anchor_points,
                   highlight=(state.get('advice') or {}).get('joints', ()))
        observation = ('淡虚线为短时延续 · 不计动作' if held else
                       '虚线为弱观测 · 镜像显示' if weak else
                       '{} 个主要关节 · 显示已平滑'.format(visible))
    else:
        view._text(cr, '等待新的关节观测', 700, 225, 16, color=MUTED, width=214, centered=True)
        view._text(cr, '缺失部位不补画\n也不当作动作错误', 700, 264, 12,
                   color=MUTED, width=214, centered=True, height=38)
        observation = '请让本式所需的主要关节入镜'
    view._text(cr, observation, 698, 351, 10, color=MUTED, width=218, centered=True, height=17)
    advice = state.get('advice') or {}
    note = ('预备式只做入镜与站姿准备，不记录上举次数。' if identity['preparation'] else
            advice.get('display_text') or advice.get('text') or state.get('feedback') or '动作建议以当前可见关节为依据。')
    view._text(cr, note, 700, 373, 11,
               color=GOLD if advice.get('kind') in ('suggestion','tentative') else MUTED, width=214, height=33)
    a,b,c,d = INSTRUCTION_BOX
    cr.set_source_rgba(*GREEN,.055);cr.rectangle(a,b,c-a,d-b);cr.fill()
    cr.set_source_rgb(*CINNABAR);cr.rectangle(a,b+4,3,d-b-8);cr.fill()
    view._text(cr, identity['instruction'], 48, 420, 18, color=INK, bold=True, width=864, height=28)
    fraction = min(1., max(0., _number(state.get('lesson_progress', state.get('progress')))))
    cr.set_source_rgba(*GREEN,.12);cr.rectangle(34,110,892,3);cr.fill()
    cr.set_source_rgb(*GOLD);cr.rectangle(34,110,892*fraction,3);cr.fill()
    if state.get('status') == 'paused':
        cr.set_source_rgba(*PAPER,.83);cr.rectangle(34,119,892,291);cr.fill()
        view._text(cr, '已暂停', 339, 225, 28, color=INK, bold=True, width=282, centered=True)
        view._text(cr, 'Menu 或下方继续按钮恢复练习', 295, 270, 14,
                   color=MUTED, width=370, centered=True)
    view._buttons(cr)
    return dict(identity=identity, reference_frame=REFERENCE_FRAME,
                observed_body=OBSERVED_BODY, visible_joints=visible, weak_joints=weak,
                full_reference_frame_preserved=True, reference_video_drawn=bool(fit),
                reference_skeleton_drawn=True,reference_synced=reference['synced'],reference_phase=reference['phase_label'])
