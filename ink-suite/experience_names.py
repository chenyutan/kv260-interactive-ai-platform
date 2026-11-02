"""One public display name per stable experience identifier."""
TITLES = {
    'ink': '身随墨动', 'sword': '御剑山河', 'fight': '格斗',
    'fruit': '水果忍者', 'taiko': '鼓动山河', 'platformer': '超级玛丽',
    'runner': '地铁跑酷', 'pal': '仙剑奇侠传', 'kart': '赛车',
}

CONTROL_NAMES = {'body': '体感游戏', 'gamepad': '游戏手柄'}

GAMEPAD_HELP = {
    'ink': ('LB / RB 选择笔画，A 落墨；左摇杆或扳机调节笔墨速度。',),
    'sword': ('左摇杆移动飞剑，自动射击；A 开始，能量满后 X / RB 清屏攻击。',),
    'fight': ('左摇杆进退 / 蹲下，A 跳跃；X 出拳、B 踢腿、Y 双掌。', 'LB 防御，能量满后 RB 释放必杀技。'),
    'fruit': ('左摇杆移动刀光，按住 RT 或 A 挥刀；X 开始。',),
    'taiko': ('X 左鼓、B 右鼓、Y 胸前拍掌轨、A 双鼓合击。',),
    'platformer': ('左摇杆或方向键移动；A 跳跃，B / 下方向进入管道，X 开始或确认。',),
    'runner': ('左摇杆或方向键左右换道；A 跳跃，B / 下方向滑铲，X 确认。',),
    'pal': ('左摇杆 / 方向键行走；A 确认或对话，B 取消，X 搜索，Y 菜单。',),
    'kart': ('左摇杆转向，自动加速；LT / 下方向刹车，B / LB 漂移。', 'X / RB 使用道具；A 开始或确认。'),
}
COMMON_GAMEPAD_HELP = (
    'Menu 键呼出二级菜单；方向键选择、A 确认、B 返回。',
    '右摇杆移动鼠标，按右摇杆 R3 点击；View + R3 右键。',
)

from thextech.selection import selected as _thextech_selected
if _thextech_selected():
    GAMEPAD_HELP['platformer']=(
        '左摇杆/方向键走路；A 跳跃、X 跑步/发射、B 特殊跳；上进门、下进管道。',
        '地图和对话中 A 确认、B 返回；Menu 打开体验菜单。')
