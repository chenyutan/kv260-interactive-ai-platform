"""Short native creation messages; no artwork, input or transport changes."""
import math


# Keep labels aligned with the renderer's resolved motif for each action.
# Mixed A06 follows the latest established bamboo/plum; explicit themes override it.
BRUSH_NAMES = {
    'landscape': ('远山', '主峰', '双峰', '斜坡', '水岸', '云雾'),
    'bamboo': ('竹丛', '主竹干', '双竹', '弯竹', '叶簇', '衬叶'),
    'plum': ('展开枝', '向上主枝', '分枝', '弯枝', '短枝', '梅花'),
    'mixed': ('山/飞鸟', '竹', '梅枝', '斜坡/岸苇', '水岸/添笔', '添叶/花'),
}
GENERIC_MESSAGES = frozenset((
    '体感控制', '手柄模式：LB/RB选择画法，按A落墨。',
))


def short_message(value, limit=96):
    if not isinstance(value, str):
        return ''
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit-1] + '…'


def ink_message(state):
    """Retain observed progress/results/errors, with old-packet fallback."""
    if not isinstance(state, dict):
        return ''
    message = short_message(state.get('message'))
    return '' if message in GENERIC_MESSAGES else message


def brush_name(theme, action):
    names = BRUSH_NAMES.get(theme, BRUSH_NAMES['mixed'])
    if action in ('A01', 'A02', 'A03', 'A04', 'A05', 'A06'):
        return names[int(action[1:])-1]
    return short_message(action) or '待选择'


def creation_header(theme, event_count=None, animation=None):
    """Presentation queue is read from the real GUI controller, not event_count."""
    theme_name = {'mixed': '混合', 'landscape': '山水', 'bamboo': '竹', 'plum': '梅'}.get(theme, '混合')
    text = '人体水墨 · ' + theme_name + '主题'
    pending = getattr(animation, 'pending', ())
    try:
        waiting = len(pending)
    except TypeError:
        waiting = 0
    if getattr(animation, 'player', None) is not None:
        text += ' · 逐笔绘制中'
        if waiting:
            text += ' · 后面还有%d次' % waiting
    elif waiting:
        text += ' · 已接收%d次，准备绘制' % waiting
    elif type(event_count) is int and event_count >= 0:
        text += ' · 共%d次创作' % event_count
    return text


def hide_embedded_ink_controls(app):
    """Remove only the known duplicate rows; the host owns confirmed actions.

    Native Display releases did not expose a toolbar attribute. Match their
    actual button labels conservatively, keeping standalone Display unchanged.
    no_show_all prevents a later Plug.show_all from restoring the controls.
    """
    hidden = []
    title = getattr(app, 'title', None)
    header = title.get_parent() if title is not None else None
    if header is not None:
        for child in header.get_children():
            label = getattr(child, 'get_label', lambda: None)()
            if child is not title and label in ('动作说明', '扫码作品'):
                child.set_no_show_all(True)
                child.hide()
                hidden.append(child)
    window = getattr(app, 'window', None)
    root = window.get_child() if window is not None else None
    if root is not None:
        for row in root.get_children():
            children = getattr(row, 'get_children', lambda: ())()
            labels = tuple(getattr(child, 'get_label', lambda: None)() for child in children)
            if labels == ('混合', '山水', '竹', '梅', '撤销', '清空', '保存', '退出'):
                row.set_no_show_all(True)
                row.hide()
                hidden.append(row)
    return tuple(hidden)


def menu_message(suite, now, fallback):
    """A live operation notice also owns the gamepad menu's message row."""
    deadline = getattr(suite, 'notice_until', 0.)
    message = short_message(getattr(suite, 'action_notice', None))
    if (getattr(suite, 'menu_level', None) == 'actions' and message
            and isinstance(deadline, (int, float)) and math.isfinite(deadline)
            and now < deadline):
        return message
    return fallback
