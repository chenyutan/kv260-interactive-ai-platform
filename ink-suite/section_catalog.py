"""Pure menu content and hit geometry for the three top-level sections.

Stable experience identifiers are shared with the existing launch/report code.
The health section exposes implemented courses; planned courses are not
clickable placeholders.
"""
import math

SECTIONS=('home','entertainment','health','creation')
SECTION_CHOICES=('section_entertainment','section_health','section_creation')
ENTERTAINMENT=('sword','fight','fruit','taiko','platformer','runner','pal','kart')
CONTENT={
    'home':SECTION_CHOICES,
    'entertainment':ENTERTAINMENT,
    'health':('baduanjin','taichi'),
    'creation':('ink','photo_ink'),
}
HEADINGS={'home':'墨境','entertainment':'娱乐','health':'健康','creation':'创作'}
SUBHEADINGS={
    'home':'娱乐 · 健康 · 创作',
    'entertainment':'八种游戏 · 体感或手柄操作',
    'health':'看示范、跟着练，记录自己的进步',
    'creation':'动作绘画，或把风景照片制作成水墨作品',
}
TITLES={
    'section_entertainment':'娱乐','section_health':'健康','section_creation':'创作',
    'section_home':'返回主菜单','pose_view':'摄像头 · 骨架',
    'visitor_download':'扫码下载','visitor_finish':'结束游玩','ai_assistant':'AI 助手',
    'sword':'御剑山河','fight':'格斗','fruit':'水果忍者','taiko':'太鼓音游',
    'platformer':'超级玛丽','runner':'地铁跑酷','pal':'仙剑奇侠传','kart':'赛车',
    'ink':'人体水墨','photo_ink':'照片水墨','baduanjin':'八段锦指导','taichi':'太极指导',
}
DESCRIPTIONS={
    'section_entertainment':'体感与手柄游戏','section_health':'八段锦与太极跟练',
    'section_creation':'动作绘画与作品收藏','pose_view':'查看识别状态',
    'visitor_download':'作品 / 成绩','visitor_finish':'归档本次记录',
    'baduanjin':'分解学习 · 完整跟练 · 自主练习',
    'taichi':'二十四式 · 示范与基础跟练',
    'ink':'动作控制笔墨，逐笔完成画卷',
    'photo_ink':'手机上传或板端选图，制作水墨作品',
}
PUBLIC_CHOICES=('pose_view','visitor_download','visitor_finish')
# Public tools are primary, easily reached entries: 271 x 80 at 960 x 540.
# The card grid and status line reserve space above this shared hit geometry.
FOOTER_RECTS=((.055,.817,.337,.965),(.359,.817,.641,.965),(.663,.817,.945,.965))
STATUS_TOP=.763
# Visible button and hand hit target share this 202 x 54 design-pixel area.
# Reserve the right of the header rather than shrinking the game cards.
BACK_RECT=(.735,.055,.945,.155)
AI_RECT=(.510,.055,.715,.155)
HOME_AI_RECT=(.370,.055,.575,.155)


def _section(section):
    if section not in SECTIONS:raise ValueError('Unknown menu section: '+str(section))
    return section


def content_rectangles(section='home'):
    section=_section(section)
    if section=='home':return ((.055,.235,.337,.740),(.359,.235,.641,.740),(.663,.235,.945,.740))
    if section=='entertainment':
        return tuple((.055+column*.225,.235+row*.267,.270+column*.225,.472+row*.267)
                     for row in range(2) for column in range(4))
    if section in ('health','creation'):return ((.055,.255,.489,.740),(.511,.255,.945,.740))
    return ((.055,.255,.655,.740),)


def choices(section='home',visitor_controls=False):
    section=_section(section)
    return CONTENT[section]+(() if section=='home' else ('section_home',))+('ai_assistant',)+PUBLIC_CHOICES[:3 if visitor_controls else 1]


def rectangles(section='home',visitor_controls=False):
    section=_section(section)
    return content_rectangles(section)+(() if section=='home' else (BACK_RECT,))+(HOME_AI_RECT if section=='home' else AI_RECT,)+FOOTER_RECTS[:3 if visitor_controls else 1]


def hit_index(x,y,section='home',visitor_controls=False):
    try:x,y=float(x),float(y)
    except (TypeError,ValueError):return None
    if not math.isfinite(x) or not math.isfinite(y):return None
    for index,(left,top,right,bottom) in enumerate(rectangles(section,visitor_controls)):
        if left<=x<right and top<=y<bottom:return index
    return None


def is_navigation(choice):
    return choice.startswith('section_') or choice in PUBLIC_CHOICES or choice=='ai_assistant'


def available_without_pose(choice):
    # A course can show its demonstration and records with no live skeleton.
    return is_navigation(choice) or choice in ('baduanjin','taichi','photo_ink')


def section_for_choice(choice):
    if choice=='section_home':return 'home'
    if choice in SECTION_CHOICES:return choice.removeprefix('section_')
    return None
