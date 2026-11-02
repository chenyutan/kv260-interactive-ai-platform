"""Small, fixed menu catalog. No UI, transport, camera or game side effects."""

from experience_names import TITLES, GAMEPAD_HELP, COMMON_GAMEPAD_HELP
from entry_help import BODY, RULES
from thextech.selection import selected as _thextech_selected

def item(id,title,subtitle='',danger=False):
    return dict(id=id,title=title,subtitle=subtitle,danger=danger)

HELP={mode:(*BODY[mode], *RULES[mode],
              '右臂在身体侧面转两圈可暂停并打开菜单；右手停留2秒选择。') for mode in TITLES}


CONFIRM={
 'restart':('重新开始这一局？','当前游戏进度会结束，新一局从头开始。'),
 'new_scroll':('新建画卷？','当前画卷会清空。未保存的作品请先保存。'),
 'rules_arcade':('切换街机规则？','规则切换会重新开始当前游戏。'),
 'rules_classic':('切换经典规则？','规则切换会重新开始当前游戏。'),
 'difficulty_easy':('选择简单难度？','切换后重新开始当前曲段。'),
 'difficulty_normal':('选择普通难度？','切换后重新开始当前曲段。'),
 'difficulty_hard':('选择困难难度？','左右交替更紧凑；切换后重新开始当前曲段。'),
 'judgment_relaxed':('改为体感判定？','增加命中档、放宽合击；重新开始当前曲段。'),
 'judgment_strict':('改为严格判定？','恢复原完美、良好与合击窗口；重新开始当前曲段。'),
 'practice_full':('演奏完整曲目？','从头开始完整的 72 秒挑战。'),
 'practice_intro':('练习入门鼓点？','练习开篇左右鼓，结束后单独结算。'),
 'practice_alternate':('练习交替与合击？','先播放四拍准备音乐，再开始这一曲段。'),
 'practice_finale':('练习结尾部分？','先播放四拍准备音乐，再挑战结尾曲段。'),
 'silent':('进入静音练习？','保留曲目当前位置，切换为无声计时。'),
 'retry_audio':('重新连接声音？','重新准备音频，成功后从当前位置继续。'),
    'quit':('退出墨境应用？','回到桌面；重新打开应用需使用桌面入口。')}

def page(mode,name='root',control_mode='body'):
    if mode not in TITLES:raise ValueError('Unknown experience')
    title=TITLES[mode]+' · 体验菜单'
    if name=='root':
        if mode=='ink':
            items=[item('resume','继续创作','收起菜单，回到画卷'),item('save','保存作品','保存到板卡并显示可用的下载二维码'),
                   item('undo','撤回一笔','撤回最近一次动作的落墨'),item('new_scroll','新画卷','清空前会再次确认',True),
                   item('options','主题与说明','混合、山水、竹、梅与玩法说明'),item('home','返回主菜单','选择其他体验')]
        else:
            items=[item('resume','继续游戏','恢复当前对局或结算画面'),item('restart','重新开始','从头开始，需再次确认',True),
                   item('help','玩法说明','查看动作和游戏规则'),item('options','体验设置','规则、声音或显示设置'),
                   item('home','返回主菜单','选择其他体验'),item('system','显示与退出','全屏切换、退出应用')]
            if mode=='taiko':
                items[2]=item('taiko_timing','延迟校准','偏早推后、偏晚提前；每次25毫秒')
                items[3]=item('options','鼓谱与判定','三档鼓谱、体感或严格判定、分段练习')
            if mode=='platformer' and _thextech_selected():
                items[1]=item('restart','重开当前关卡','保留冒险存档；在关卡中可用',True)
    elif name=='options':
        title=TITLES[mode]+' · 体验设置'
        if mode=='ink':
            items=[item('themes','选择主题','混合、山水、竹或梅'),item('help','玩法说明','查看动作与笔墨映射'),
                   item('ai_assistant','AI 助手','操作讲解、创作方案与建议'),
                   item('system','显示与退出','全屏切换、退出应用'),item('back','返回菜单','保存、撤回或继续创作')]
        elif mode=='fruit':
            items=[item('rules_arcade','街机规则','切换后重新开始'),item('rules_classic','经典规则','切换后重新开始'),
                   item('mirror_on','开启镜像','调整刀迹左右方向'),item('mirror_off','关闭镜像','使用另一种左右方向'),
                   item('ai_assistant','AI 助手','讲解本项目玩法与操作'),item('back','返回菜单')]
        elif mode=='taiko':
            items=[item('taiko_difficulty','鼓谱与判定','三档鼓谱、体感或严格判定'),item('taiko_practice','分段练习','练左右鼓、交替合击或终章'),
                   item('ai_assistant','AI 助手','击鼓玩法、操作与练习建议'),item('audio','声音设置','音乐、音效与音量'),
                   item('silent','无声练习','保留进度，明确切换为静音计时'),item('back','返回菜单')]
        else:
            items=[item('fullscreen','切换全屏','在窗口与全屏之间切换'),item('help','玩法说明'),
                   item('ai_assistant','AI 助手','讲解本项目玩法与操作'),
                   item('quit','退出应用','退出前会再次确认',True),item('back','返回菜单')]
    elif name=='taiko_difficulty' and mode=='taiko':
        title='鼓动山河 · 鼓谱与判定'
        items=[item('difficulty_easy','简单','留足抬手时间，熟悉左右鼓'),item('difficulty_normal','普通','交替击鼓与双手合击'),
               item('difficulty_hard','困难','连续交替，挑战高准确率'),
               item('judgment_relaxed','体感判定','完美110、良好220、命中350毫秒'),
               item('judgment_strict','严格判定','原有完美110、良好220毫秒'),item('back','返回菜单')]
    elif name=='taiko_practice' and mode=='taiko':
        title='鼓动山河 · 分段练习'
        items=[item('practice_full','完整演奏','72 秒完整谱面'),item('practice_intro','左右入门','开篇 12 秒'),
               item('practice_alternate','交替合击','四拍准备后，练习中段'),item('practice_finale','结尾练习','四拍准备后，练习收尾'),
               item('back','返回菜单')]
    elif name=='taiko_timing' and mode=='taiko':
        title='鼓动山河 · 延迟校准'
        items=[item('timing_earlier','总是偏晚','将击鼓判定提前 25 毫秒'),item('timing_later','总是偏早','将击鼓判定推后 25 毫秒'),
               item('timing_preset_m100','偏早 · 推后100','直接设为 -100 毫秒补偿'),
               item('timing_preset_m150','偏早 · 推后150','直接设为 -150 毫秒补偿'),
               item('timing_reset','清零校准','恢复 0 毫秒补偿'),item('back','返回菜单')]
    elif name=='themes' and mode=='ink':
        title='身随墨动 · 选择主题'
        items=[item('theme_mixed','混合主题','山水、竹梅自由搭配'),item('theme_landscape','山水主题','山形、云雾与点景'),
               item('theme_bamboo','竹主题','画竹、添叶'),item('theme_plum','梅主题','画枝、添花'),item('back','返回菜单')]
    elif name=='system':
        title='墨境 · 显示与退出'
        items=[item('connections','设备与联网','搜索 Wi‑Fi、重连摄像头或音响'),item('fullscreen','切换全屏','在窗口与全屏之间切换'),item('quit','退出应用','退出前会再次确认',True),item('back','返回菜单')]
        items.insert(0,item('control_mode','体感 / 手柄','重新选择当前游戏的控制方式'))
        if mode in ('ink','fruit','fight','taiko','sword','platformer','runner','kart'):items.insert(0,item('audio','声音设置','音乐、音效、音量与重试输出'))
    elif name=='audio' and mode in ('ink','fruit','fight','taiko','sword','platformer','runner','kart'):
        title=TITLES[mode]+' · 声音设置'
        items=[item('audio_low','低音量','总音量 28%'),item('audio_normal','标准音量','总音量 55%'),
               item('audio_mute','全部静音','计时与游戏继续'),item('audio_unmute','恢复声音','保留音乐与音效各自设置'),
               item('audio_options','音乐与音效','分别开关，或重试音频设备'),item('back','返回菜单')]
        if mode=='platformer' and _thextech_selected():
            items=[item('audio_mute','全部静音','游戏继续；暂停菜单内本身静音'),
                   item('audio_unmute','恢复声音','继续游戏后恢复原版音乐与音效'),
                   item('back','返回菜单')]
    elif name=='audio_options' and mode in ('ink','fruit','fight','taiko','sword','platformer','runner','kart'):
        title=TITLES[mode]+' · 音乐与音效'
        items=[item('audio_music_on','开启音乐'),item('audio_music_off','关闭音乐','不改变音游谱面时钟'),
               item('audio_effects_on','开启音效'),item('audio_effects_off','关闭音效'),
               item('audio_retry','重试输出','重新尝试连接声音设备'),item('back','返回菜单')]
    elif name=='help':return dict(title=TITLES[mode]+' · 玩法说明',help=(GAMEPAD_HELP[mode]+COMMON_GAMEPAD_HELP if control_mode=='gamepad' else HELP[mode]),items=[item('back','返回菜单')])
    elif name.startswith('confirm:'):
        action=name.split(':',1)[1]
        if action not in CONFIRM:raise ValueError('Unknown confirmation')
        title,explanation=CONFIRM[action]
        if mode=='platformer' and action=='restart' and _thextech_selected():
            title,explanation='重开当前关卡？','仅重开正在游玩的关卡，保留冒险存档。地图和对话中不可重开。'
        items=[item('back','取消','保留当前进度'),item('confirm:'+action,'确认执行',explanation,True)]
    elif name=='uncertain':
        title='未收到操作完成回执'
        items=[item('home','返回主菜单','重新进入体验后确认状态；避免重复执行')]
    elif name=='error':
        title='体验暂未打开'
        items=[item('retry','重新打开','重试刚才的体验'),item('home','返回主菜单','选择其他体验')]
    else:raise ValueError('Unknown menu page')
    return dict(title=title,items=items)

def actions(mode):
    pages=('root','options','system')
    if mode=='ink':pages+=('themes',)
    if mode=='taiko':pages+=('taiko_difficulty','taiko_practice','taiko_timing')
    if mode in ('ink','fruit','fight','taiko','sword','platformer','runner','kart'):pages+=('audio','audio_options')
    return {i['id'] for p in pages for i in page(mode,p)['items']}
