"""Local project knowledge; cloud selects offered facts, never invents controls.

Gameplay text comes from the same entry cards shown to the player. Health
facts describe the implemented course/UI, not medical or pose diagnoses.
No visitor image, raw pose, name, file path or stored score is read here.
"""
import copy
from .contracts import AIError

EXPERIENCE_IDS = ('sword','fight','fruit','taiko','platformer','runner','pal','kart',
                  'baduanjin','taichi','ink','photo_ink')
PROJECT_IDS = ('system',)+EXPERIENCE_IDS
SYSTEM_PRESETS = (
    dict(id='rules',title='项目概览',description='娱乐、健康与创作'),
    dict(id='controls',title='技术原理',description='KV260 · PS/PL · AI'),
    dict(id='tips',title='应用场景',description='展示、体验与成果留存'),
)
GUIDE_PRESETS = (
    dict(id='rules',title='怎么玩',description='了解目标、规则与流程'),
    dict(id='controls',title='怎么操作',description='体感动作与手柄按键'),
    dict(id='tips',title='给点建议',description='从简单练习开始'),
)
_TIPS = {
    'sword': ('先观察敌弹空隙，再移动飞剑；不必为了拾取道具进入密集弹幕。',
              '先熟悉右手或左摇杆控制，自动射击让你可以专心躲避。',
              '留意生命与能量，能量满后再选择合适时机清屏。'),
    'fight': ('先分别练习出拳、踢腿和防御，再尝试拳腿衔接。',
              '左右侧移后停住，观察角色是否停下，再进行下一次攻击。',
              '被攻击时可以尝试防御，不必一直抢攻。'),
    'fruit': ('先避开炸弹，再寻找一刀能划过的相邻水果。',
              '尽量连贯地挥刀，观察刀光是否经过果体。',
              '手柄模式要按住 RT 或 A 才挥刀，左摇杆用来移动刀光。'),
    'taiko': ('先选左右入门分段练习，再练习双鼓合击和胸前拍掌。',
              '击鼓后抬手、拍掌后分开双手，给下一次动作留出准备。',
              '若持续感觉偏早或偏晚，可在菜单调整延迟；仅凭一次未命中不能确定补偿值。'),
    'platformer': ('先熟悉走路、跳跃和停止，再探索高处或隐藏路线。',
                   '看到前方间隙时提前准备跳跃，先观察落点。',
                   '可以打开开始前操作说明，确认当前引擎对应的按键。'),
    'runner': ('观察前方障碍与车顶间隙，提前准备换道或跳跃。',
               '体感站位对应左、中、右三条跑道，身体回中就回中道；起跳前可轻微屈膝，明显下蹲才触发滑铲。',
               '先练习跳过路障和滑过横梁，再尝试连续车顶路线。'),
    'pal': ('先与附近角色对话、调查场景，再决定下一步去哪里。',
            '退出前使用游戏内菜单存档；应用重新开始不等于保存当前进度。',
            '不熟悉体感方向时，可使用方向键或左摇杆探索。'),
    'kart': ('先熟悉转向并保持在赛道内，再尝试漂移。',
             '漂移蓄力后松开可加速，先在单个弯道练习这个过程。',
             '看清前方路线后再使用道具，注意自己的名次和圈数。'),
    'ink': ('先完成竹干或梅枝，再添加对应的竹叶或梅花。',
            '每次落墨后看一下构图，再决定下一笔，不必一次铺满画面。',
            '想组合山、竹、梅，可使用 AI 创作方案；采用方案只设置主题，仍需自己落墨。'),
}


def _validate(project):
    if not isinstance(project,str) or project not in PROJECT_IDS:
        raise AIError('invalid_project','请选择一个现有项目。')
    return project


def _entry(project):
    from section_catalog import TITLES,CONTENT
    project=_validate(project)
    if project=='system':return _system_entry()
    section=next(section for section in ('entertainment','health','creation') if project in CONTENT[section])
    facts=[]
    def add(group,values):
        for n,value in enumerate(values,1):
            facts.append(dict(id=f'{project}.{group}.{n}',text=str(value),category=group))
    if project in _TIPS:
        from entry_help import BODY,RULES
        from experience_names import GAMEPAD_HELP,COMMON_GAMEPAD_HELP
        add('rules',RULES[project])
        add('body',('体感：'+value for value in BODY[project]))
        add('gamepad',('手柄：'+value for value in GAMEPAD_HELP[project]))
        add('menu',COMMON_GAMEPAD_HELP)
        tips=_TIPS[project]
    elif section=='health':
        from health.course_registry import get_course
        course=get_course(project)
        add('rules',('先选择“分解学习”“完整跟练”或“自主练习”，也可以先选一式再开始。',
                     '分解学习先播放真人示范；选择“看示意并跟练”后，可同时看参考视频小窗、示意骨架和自己的动作。',
                     '参考小窗静音循环当前示范，不与自己的动作逐帧同步；“回看视频”可放大观看。',
                     course['assessment_notice']))
        add('controls',('方向键或左摇杆选择按钮，A 确认；B 逐级返回，Menu 暂停或继续。',
                        '使用“选择动作”切换课程动作；“上一式”“下一式”切换练习，视频可暂停、继续或从头播放。',
                        '跟练时让本式所需肩、肘、腕、髋及腿部关节清晰入镜；关节不可靠时可以继续跟练，但不应把缺失点当作动作错误。'))
        for lesson in course['lessons']:
            facts.append(dict(id=project+'.lesson.'+lesson['id'],category='lesson',
                text=lesson['name']+'：'+' '.join(lesson['instructions'])+' '+lesson['assessment_note']))
        tips=(course['notice'],'先看一段示范，再用舒适幅度慢慢跟练；不必追求与示意骨架完全重合。',
              '看不清动作时可回看大视频，或暂停后选一式反复熟悉；AI 只解释现有课程，不能诊断身体或评定医疗效果。')
    else:
        add('rules',('导入自己的 JPG、PNG 或 WebP 静态照片，选择“青绿淡彩”或“水墨”，再点“开始制作”。',
                     '可从板卡或 U盘选择图片，也可用手机与板卡连接同一局域网后扫码上传；照片由 KV260 本地处理。',
                     '完成后可切换“查看原图”比较效果，并在历史中选择作品；手机页面可下载原图与处理结果。',
                     '照片最长边按 1920 像素处理；上传上限16 MB、2400万像素。处理时间随照片而变化。'))
        add('controls',('方向键或左摇杆选择按钮，A 确认；左右切换画风或历史，B 返回。',
                        '选图窗口中上下选文件，A 打开目录或选照片，LB 上一级，RB 刷新；右摇杆移动鼠标，R3 点击。'))
        tips=('选择主体清晰、前后层次明确的照片，先比较青绿淡彩和水墨两种效果。',
              '原照片已模糊时，放大不能恢复不存在的细节；可换清晰原图再制作。',
              '热点暂不可用时，仍可从板卡或 U盘选照片；手机上传需要可用的同一局域网。')
    return dict(id=project,title=TITLES[project],section=section,facts=facts,
        tips=[dict(id=f'{project}.tip.{n}',text=value) for n,value in enumerate(tips,1)])


def _system_entry():
    """Auditable product overview; no live measurements or medical claims."""
    groups={
        'rules':(
            '本项目以 AMD Kria KV260 为核心，把娱乐、健康、创作整合到一个体感与手柄双模式的互动终端。',
            '娱乐包含八种游戏；健康提供八段锦与二十四式太极的视频示范、骨架跟练和基础动作提示；创作提供人体水墨与照片水墨，共十二项体验。',
            '用户可通过手柄或右手停留选择项目，游玩后在同一局域网用手机查看自己的作品、成绩图片与练习记录。',
            '人体水墨把识别出的动作转成逐笔绘画；照片水墨在 KV260 上处理用户导入的静态照片。'),
        'controls':(
            '图像由 USB 摄像头采集，经处理系统 PS 写入内存；团队自研的 FPGA PL 前处理完成 ROI 采样、YUYV 颜色转换、缩放和模型所需的 INT8 量化。',
            '人体姿态感知采用 AMD Vitis AI 2.5 官方 Hourglass MPII 预训练 INT8 模型，由 PL 中的 DPU 执行。网络不是团队从零训练，也不是由 DeepSeek 识别关节。',
            'ARM 处理器对关节热图解码，再结合关节质量、身体比例和时间序列判断动作，转成移动、击打、挥刀与绘画等交互。Mali GPU 负责适合的三维绘制任务。',
            'DeepSeek 由 KV260 后台直接调用，负责项目讲解、创作方案与成绩复盘。回答使用本地项目事实和结构化结果校验；它不会直接操纵游戏、修改分数或替用户落墨。',
            '系统把摄像头感知、姿态推理、动作判断、显示、音频与手机服务分工处理；联网服务不可用时，本地体验与固定操作说明仍可使用。'),
        'tips':(
            '科技馆、博物馆或校园展览可让观众用身体动作参与游戏、完成一幅水墨作品，再用手机带走本次成果。',
            '公共体验区可用手柄与体感两种输入照顾不同操作习惯；项目说明、课程示范与 AI 快捷问答能减少长时间打字。',
            '健康板块适合动作学习与基础跟练展示，真人视频和骨架提示互相补充；它不提供医疗诊断，也不以跟练分数证明健康效果。',
            '创作板块适合美育互动：体验动作与笔墨的对应关系，比较照片处理前后的画面，并收藏作品。'),
    }
    facts=[dict(id=f'system.{group}.{n}',text=value,category=group)
           for group,values in groups.items() for n,value in enumerate(values,1)]
    tips=(
        '先选择一个板块体验，再用“技术原理”了解这一步由 PS、PL、DPU 还是云端 AI 完成。',
        '展示时可先讲“看见动作—理解动作—产生反馈—保存成果”的完整流程，再展示具体项目。',
        '演示结果以实际运行和用户体验为准；固定介绍不提供未经本轮测量的帧率、时延或健康效果承诺。')
    return dict(id='system',title='项目介绍',section='system',facts=facts,
                tips=[dict(id=f'system.tip.{n}',text=value) for n,value in enumerate(tips,1)])


def guide_evidence(project,preset='rules'):
    if preset not in ('rules','controls','tips'):
        raise AIError('invalid_preset','请选择玩法、操作或建议。')
    entry=_entry(project)
    evidence=dict(project=entry['id'],title=entry['title'],section=entry['section'],focus=preset,
        facts=copy.deepcopy(entry['facts']),tips=copy.deepcopy(entry['tips']),limitations=[
            '只解释该项目已经实现的操作、规则和本地建议，不生成新的按键、功能或游戏指令。',
            '没有本人的实时姿态、照片、练习过程或健康信息，不能诊断身体、纠正不可见姿势或承诺效果。',
            '网络不可用时可看本地说明，不能声称收到云端回答。'])
    if project=='system':
        evidence['focus_description']=next(item['title'] for item in SYSTEM_PRESETS if item['id']==preset)
    return evidence


def project_help(project):
    """Always available offline and explicitly local, not a cloud answer."""
    entry=_entry(project)
    if project=='system':
        sections=[]
        for group,label in (('rules','项目概览'),('controls','技术原理'),('tips','应用场景')):
            sections.append(label+'\n'+'\n'.join(row['text'] for row in entry['facts'] if row['category']==group))
        return dict(title='项目介绍 · 本地说明',summary='\n\n'.join(sections),
                    tips=[row['text'] for row in entry['tips']])
    rules=[row['text'] for row in entry['facts'] if row['category']=='rules']
    controls=[row['text'] for row in entry['facts'] if row['category'] in ('body','gamepad','controls','menu')]
    return dict(title=entry['title']+' · 本地说明',summary='\n'.join(rules),
        tips=controls+[row['text'] for row in entry['tips']])


def project_catalog():
    return [dict(id=project,title=(entry:=_entry(project))['title'],section=entry['section'],
                 help=project_help(project)) for project in PROJECT_IDS]
