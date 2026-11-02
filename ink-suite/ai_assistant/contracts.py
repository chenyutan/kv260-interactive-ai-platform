"""Public AI job contracts and strict, locally checked output normalization."""
import json
import re
import unicodedata

MODEL = 'deepseek-flash'
THINKING = 'disabled'
JOB_ID = re.compile(r'^[a-f0-9]{32}$')
KINDS = ('creation', 'review', 'guide', 'health')
STATUSES = ('queued', 'running', 'ready', 'error')
THEME_ALIASES = {'山水': 'landscape', '竹': 'bamboo', '竹子': 'bamboo',
                 '梅': 'plum', '梅花': 'plum', '混合': 'mixed'}


class AIError(ValueError):
    def __init__(self, code, message, status=400):
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)
        super().__init__(self.message)


def bounded_text(value, maximum, *, required=False):
    if not isinstance(value, str):
        raise AIError('invalid_text', '文字格式不正确。')
    value = unicodedata.normalize('NFKC', value).strip()
    if len(value) > maximum or any(unicodedata.category(c).startswith('C') and c not in '\n\t' for c in value):
        raise AIError('invalid_text', '文字太长或包含不支持的字符。')
    if required and not value:
        raise AIError('invalid_text', '请填写需要的文字。')
    return value


def parse_json(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or len(value.encode('utf-8')) > 65536:
        raise AIError('invalid_response', 'AI 返回格式异常，请稍后重试。', 502)
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError('duplicate field')
            result[key] = item
        return result
    try:
        parsed = json.loads(value, object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, TypeError, RecursionError):
        raise AIError('invalid_response', 'AI 返回格式异常，请稍后重试。', 502) from None
    if not isinstance(parsed, dict):
        raise AIError('invalid_response', 'AI 返回格式异常，请稍后重试。', 502)
    return parsed


def creation_result(value):
    from .catalog import ACTIONS, THEMES, instruction, resolved_theme
    value = parse_json(value)
    if set(value) - {'title', 'summary', 'steps'}:
        raise AIError('invalid_plan', '创作建议格式不正确，请重试。', 502)
    title = bounded_text(value.get('title'), 36, required=True)
    summary = bounded_text(value.get('summary'), 220, required=True)
    raw_steps = value.get('steps')
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 4:
        raise AIError('invalid_plan', '创作步骤需为 1 到 4 步。', 502)
    parents = set()
    steps = []
    for raw in raw_steps:
        if not isinstance(raw, dict) or set(raw) - {'theme', 'action', 'instruction'}:
            raise AIError('invalid_plan', '创作建议包含不支持的操作。', 502)
        theme, action = raw.get('theme'), raw.get('action')
        # Normalize spelling only. Never infer an action from prose, drop an
        # invalid step or request a second paid completion to repair the plan.
        if isinstance(theme, str):
            theme = unicodedata.normalize('NFKC', theme).strip().lower()
            theme = THEME_ALIASES.get(theme, theme)
        if isinstance(action, str):
            action = unicodedata.normalize('NFKC', action).strip().upper()
        if not isinstance(theme, str) or theme not in THEMES or not isinstance(action, str) or action not in ACTIONS:
            raise AIError('invalid_plan', '创作建议包含不支持的主题或动作。', 502)
        resolved = resolved_theme(theme, action)
        if resolved in ('bamboo', 'plum'):
            if action in ('A05', 'A06') and resolved not in parents:
                raise AIError('missing_parent', '请先安排竹干或梅枝，再安排添叶或添花。', 502)
            if action in ('A01', 'A02', 'A03', 'A04'):
                parents.add(resolved)
        # Never display or execute a model-authored operation instruction.
        steps.append(dict(theme=theme, action=action, instruction=instruction(theme, action)))
    return dict(title=title, summary=summary, steps=steps)


def creation_response(value, evidence):
    """Resolve one offered ID to immutable local steps, never model steps."""
    from .catalog import CREATION_PLANS
    value = parse_json(value)
    if set(value) != {'title', 'summary', 'plan_id'}:
        raise AIError('invalid_plan', '创作建议格式不正确，请重试。', 502)
    ident = value['plan_id']
    offered = {plan.get('id') for plan in evidence.get('plans', []) if isinstance(plan, dict)}
    if not isinstance(ident, str) or ident not in offered:
        raise AIError('unknown_plan', '创作建议不在本次可用方案中，请重试。', 502)
    plan = next((plan for plan in CREATION_PLANS if plan['id'] == ident), None)
    if plan is None:
        raise AIError('unknown_plan', '创作建议不在本次可用方案中，请重试。', 502)
    return creation_result(dict(title=value['title'], summary=value['summary'], steps=plan['steps']))


def review_result(value, evidence):
    """The model selects facts and coaching options; numbers/text stay local."""
    value = parse_json(value)
    if set(value) - {'fact_ids', 'tip_ids'}:
        raise AIError('invalid_review', '复盘建议格式不正确，请重试。', 502)
    facts, tips = evidence['facts'], evidence['tips']
    fact_map = {row['id']: row['text'] for row in facts}
    tip_map = {row['id']: row['text'] for row in tips}
    chosen, advice = value.get('fact_ids'), value.get('tip_ids')
    if (not isinstance(chosen, list) or not 1 <= len(chosen) <= 3 or
            not isinstance(advice, list) or not 1 <= len(advice) <= 3):
        raise AIError('invalid_review', '复盘建议格式不正确，请重试。', 502)
    if (any(not isinstance(i, str) or i not in fact_map for i in chosen) or
            any(not isinstance(i, str) or i not in tip_map for i in advice)):
        raise AIError('unsupported_evidence', '复盘建议缺少对应的实际记录，请重试。', 502)
    if len(set(chosen)) != len(chosen) or len(set(advice)) != len(advice):
        raise AIError('invalid_review', '复盘建议重复，请重试。', 502)
    return dict(title='本次游玩复盘', summary='；'.join(fact_map[i] for i in chosen)+'。',
                tips=[tip_map[i] for i in advice])


def guide_result(value,evidence):
    """Resolve only offered project knowledge; do not trust generated prose."""
    value=parse_json(value)
    if set(value)!={'fact_ids','tip_ids'}:
        raise AIError('invalid_guide','项目建议格式不正确，请重试。',502)
    facts={row['id']:row['text'] for row in evidence['facts']}
    tips={row['id']:row['text'] for row in evidence['tips']}
    picked,advice=value['fact_ids'],value['tip_ids']
    if (not isinstance(picked,list) or not 1<=len(picked)<=5 or
        not isinstance(advice,list) or not 1<=len(advice)<=3 or
        any(not isinstance(i,str) or i not in facts for i in picked) or
        any(not isinstance(i,str) or i not in tips for i in advice)):
        raise AIError('unsupported_guide','建议不在本项目现有说明中，请重试。',502)
    if len(set(picked))!=len(picked) or len(set(advice))!=len(advice):
        raise AIError('invalid_guide','项目建议重复，请重试。',502)
    title=(evidence['title']+' · '+evidence['focus_description'] if evidence.get('project')=='system'
           else evidence['title']+' · 操作与建议')
    return dict(title=title,summary='\n'.join(facts[i] for i in picked),
                tips=[tips[i] for i in advice])
