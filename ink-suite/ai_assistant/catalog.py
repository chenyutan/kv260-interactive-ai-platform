"""Whitelisted plans and report facts; never upload names, poses or photos."""
import copy
import math
from .contracts import AIError

ACTIONS = ('A01', 'A02', 'A03', 'A04', 'A05', 'A06')
THEMES = ('landscape', 'bamboo', 'plum', 'mixed')
THEME_NAMES = dict(landscape='山水', bamboo='竹', plum='梅', mixed='混合')
BODY_ACTIONS = dict(A01='双臂从胸前向两侧展开', A02='单臂向上举',
                    A03='双臂向上举', A06='双手向上托')
ELEMENTS = {
    'landscape': ('远山', '主峰', '双峰', '斜坡', '水岸', '云雾'),
    'bamboo': ('竹丛', '主竹干', '双竹', '弯竹', '叶簇', '衬叶'),
    'plum': ('展开枝', '向上主枝', '分枝', '弯枝', '短枝', '梅花'),
}


def _plan(ident, preset, title, description, *steps):
    return dict(id=ident, preset=preset, title=title, description=description,
                steps=[dict(theme=theme, action=action) for theme, action in steps])


# Model may select one entry, never synthesize or rearrange rendering steps.
# Each theme establishes its own trunk/branch before leaves or blossoms.
CREATION_PLANS = (
    _plan('landscape_layers', 'landscape', '层叠山水', '远山、主峰、双峰与云雾，层次较丰富。',
          ('landscape','A01'), ('landscape','A02'), ('landscape','A03'), ('landscape','A06')),
    _plan('landscape_peaks', 'landscape', '双峰云雾', '以主峰和双峰为主，最后添云雾。',
          ('landscape','A02'), ('landscape','A03'), ('landscape','A06')),
    _plan('landscape_minimal', 'landscape', '远山留白', '只画远山与云雾，保留更多留白。',
          ('landscape','A01'), ('landscape','A06')),
    _plan('bamboo_pair', 'bamboo', '双竹添叶', '主竹干与双竹相配，最后添叶。',
          ('bamboo','A02'), ('bamboo','A03'), ('bamboo','A06')),
    _plan('bamboo_grove', 'bamboo', '竹丛小景', '先画竹丛和竹干，再添叶。',
          ('bamboo','A01'), ('bamboo','A02'), ('bamboo','A06')),
    _plan('bamboo_sparse', 'bamboo', '清简一竹', '一组竹干加衬叶，步骤简短。',
          ('bamboo','A02'), ('bamboo','A06')),
    _plan('plum_fan', 'plum', '梅枝添花', '向上主枝加分枝，最后点梅花。',
          ('plum','A02'), ('plum','A03'), ('plum','A06')),
    _plan('plum_spread', 'plum', '展开梅枝', '展开枝和分枝组合，最后添花。',
          ('plum','A01'), ('plum','A03'), ('plum','A06')),
    _plan('plum_simple', 'plum', '一枝梅花', '先画向上主枝，再点梅花。',
          ('plum','A02'), ('plum','A06')),
    _plan('mixed_mountain_bamboo', 'mixed', '山间竹影', '远山旁先画竹干再添叶，以云雾收尾。',
          ('landscape','A01'), ('bamboo','A02'), ('bamboo','A06'), ('landscape','A06')),
    _plan('mixed_mountain_plum', 'mixed', '山间梅花', '远山旁先画梅枝再点梅花，以云雾收尾。',
          ('landscape','A01'), ('plum','A02'), ('plum','A06'), ('landscape','A06')),
    _plan('mixed_bamboo_plum', 'mixed', '竹梅相伴', '分别完成竹干与叶、梅枝与花。',
          ('bamboo','A02'), ('bamboo','A06'), ('plum','A03'), ('plum','A06')),
    _plan('mixed_three_scenes', 'mixed', '山竹梅小景', '远山、竹干、梅枝和梅花组合。',
          ('landscape','A01'), ('bamboo','A02'), ('plum','A03'), ('plum','A06')),
)
PRESETS = {
    'creation': (
        dict(id='landscape', title='山水画', description='用已有山形、水岸和云雾组成画作'),
        dict(id='bamboo', title='画竹', description='先画竹干，再添叶'),
        dict(id='plum', title='画梅', description='先画梅枝，再添花'),
        dict(id='mixed', title='自由搭配', description='组合现有山水、竹和梅元素'),
    ),
    'review': (
        dict(id='overview', title='本次复盘', description='看看已经完成对局的表现'),
        dict(id='improve', title='下局建议', description='选择下一次可尝试的练习方向'),
    ),
}
from .projects import GUIDE_PRESETS
PRESETS['guide'] = GUIDE_PRESETS
from .health import HEALTH_PRESETS
PRESETS['health'] = HEALTH_PRESETS
GAME_NAMES = dict(sword='御剑山河', fight='格斗', fruit='水果忍者', taiko='太鼓音游',
                  platformer='超级玛丽', runner='地铁跑酷', kart='赛车')
# Fields kept intentionally narrower than visitor.store.FIELDS.
NUMBERS = dict(score='得分', best_combo='最高连击', fruits_cut='切中水果', bombs_hit='碰到炸弹',
               missed='漏过目标', hits='命中', blocks='格挡', perfect_blocks='完美格挡',
               kills='击败敌人', graze='擦弹', distance='行进距离', coins='金币',
               completed_stages='完成关卡', enemies_defeated='击败敌人', deaths='失误',
               collisions='碰撞', drifts='漂移', items_used='使用道具', boosts='加速',
               accuracy='判定准确率', lap='圈数', total_laps='总圈数', best_lap='最快单圈',
               finish_time='完成时间')
GRADES = {'Perfect': '完美', 'Good': '良好', 'OK': '命中', 'Miss': '未命中'}
TIPS = {
    'general.retry': '可以再玩一局同样的规则，比较自己前后两次的实际结果。',
    'fruit.bombs': '下一局先避开炸弹，再寻找能连续切中的水果。',
    'fruit.combo': '尝试一次挥刀切过相邻水果，熟悉连击节奏。',
    'taiko.intro': '可选择“左右入门”分段练习，先熟悉左右下挥。',
    'taiko.alternate': '可选择“交替合击”练习，分别练习左右鼓、双鼓和胸前拍掌。',
    'taiko.timing': '若自己感觉总是偏早或偏晚，可打开延迟校准；现有成绩不足以判断具体补偿值。',
    'fight.guard': '下一局可穿插举臂防御与出拳、抬膝，不必连续抢攻。',
    'fight.link': '可以练习出拳后接抬膝，观察实际出招反馈。',
    'sword.gaps': '先观察弹幕空隙，再移动飞剑；避免为了拾取道具进入密集弹幕。',
    'runner.lookahead': '提前观察前方障碍和车顶间隙，给换道、跳跃或滑铲留出时间。',
    'platformer.checkpoint': '可以先熟悉最近的检查点，再探索金币和隐藏路线。',
    'kart.drift': '下一局可先练习弯前减小转向幅度，再尝试漂移后的加速。',
}


def presets():
    return copy.deepcopy(PRESETS)


def resolved_theme(theme, action):
    if theme != 'mixed':
        return theme
    # Only mixed mappings already documented in the public body controls are
    # unambiguous. Plans with other actions must choose an explicit theme.
    mapped = {'A01': 'landscape', 'A02': 'bamboo', 'A03': 'plum'}
    if action not in mapped:
        raise AIError('ambiguous_theme', '这一步请指定山水、竹或梅主题。', 502)
    return mapped[action]


def instruction(theme, action):
    element = ELEMENTS[resolved_theme(theme, action)][ACTIONS.index(action)]
    control = ('体感可%s；' % BODY_ACTIONS[action]) if action in BODY_ACTIONS else ''
    return ('选择%s主题。%s手柄用 LB/RB 选 %s，按 A 落墨，画出%s。'
            % (THEME_NAMES[theme], control, action, element))


def creation_evidence(preset=None):
    from .contracts import creation_result
    if preset not in (None, '', 'landscape', 'bamboo', 'plum', 'mixed'):
        raise AIError('invalid_preset', '请选择支持的创作主题。')
    plans = [copy.deepcopy(plan) for plan in CREATION_PLANS if not preset or plan['preset'] == preset]
    for plan in plans:
        creation_result(dict(title=plan['title'], summary=plan['description'], steps=plan['steps']))
    return dict(plans=plans,
                constraints=['只选择一个候选plan_id，不生成或修改步骤。',
                             '每个计划已限定为本地已有图案，所有主题分别先干枝后叶花。',
                             '不生成新素材或任意构图；步骤由用户体感或手柄实际完成。'])


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1e9


def review_evidence(view):
    """Export only locally finished games and bounded, typed facts."""
    rounds = view.get('rounds', []) if isinstance(view, dict) else []
    if not isinstance(rounds, list):
        rounds = []
    completed = [r for r in rounds if isinstance(r, dict) and r.get('status') == 'completed'
                 and r.get('game') in GAME_NAMES][-8:]
    if not completed:
        raise AIError('no_completed_rounds', '还没有已完成的游戏，先完成一局再来复盘。', 409)
    facts, games = [], set()
    for index, row in enumerate(completed, 1):
        game = row['game'];games.add(game)
        data = row.get('data') if isinstance(row.get('data'), dict) else {}
        # The label is local, not a user-provided name, title, path or token.
        label = '第%d局%s' % (index, GAME_NAMES[game])
        facts.append(dict(id='r%d.completed' % index, text=label+'已完成', game=game))
        for key, title in NUMBERS.items():
            value = row.get('score') if key == 'score' else data.get(key)
            if _number(value):
                rendered = ('%.2f' % value).rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
                unit = '%' if key == 'accuracy' else '秒' if key in ('best_lap', 'finish_time') else ''
                caption = '排名分' if game == 'fight' and key == 'score' else title
                facts.append(dict(id='r%d.%s' % (index, key), text=label+caption+' '+rendered+unit,
                                  game=game, field=key, value=value))
        counts = data.get('counts')
        if game == 'taiko' and isinstance(counts, dict):
            for key, title in GRADES.items():
                value = counts.get(key)
                if type(value) is int and 0 <= value <= 1000000:
                    facts.append(dict(id='r%d.counts.%s' % (index, key), text=label+title+' '+str(value)+' 次',
                                      game=game, field='counts.'+key, value=value))
        # Preserve rule context without sending an arbitrary stored string.
        for key, allowed in (('difficulty', ('easy','normal','hard')),
                             ('practice', ('full','intro','alternate','finale')),
                             ('judgment', ('relaxed','strict')),
                             ('mode', ('arcade','classic','thextech-aod'))):
            if data.get(key) in allowed:
                facts.append(dict(id='r%d.%s' % (index,key), text=label+' '+key+'='+data[key],
                                  game=game, field=key, value=data[key]))
    tips = [dict(id=key, text=value) for key, value in TIPS.items()
            if key.startswith('general.') or key.split('.',1)[0] in games]
    return dict(facts=facts[:200], tips=tips,
                limitations=['只有已完成游戏的本地统计，没有原始姿态、图像、姓名或完整游客标识。',
                             '未命中可能包含动作未识别，不能据此诊断用户能力或身体状态。',
                             '没有每轨早晚偏差汇总，不推测具体延迟补偿；不同玩法成绩不作直接高低比较。'])
