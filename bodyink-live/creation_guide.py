"""User-facing mappings mirror InkRenderer; no action inference happens here."""
THEMES={'mixed':'混合','landscape':'山水','bamboo':'墨竹','plum':'梅枝'}
ACTIONS={'A01':'双臂外展','A02':'单臂上举','A03':'双臂上举','A04':'身体侧倾','A06':'双手上托'}
RESULTS={
 'landscape':{'A01':'远山','A02':'主峰','A03':'双峰','A04':'斜坡','A06':'云雾'},
 'bamboo':{'A01':'竹丛','A02':'主竹干','A03':'双竹','A04':'弯竹','A06':'衬叶'},
 'plum':{'A01':'展开枝','A02':'向上主枝','A03':'分枝','A04':'弯枝','A06':'梅花'},
 'mixed':{'A01':'远山','A02':'竹干','A03':'梅枝','A04':'斜坡','A06':'有梅添花，有竹添叶，否则云雾'}}

def guide_text(theme,enabled):
 theme=theme if theme in THEMES else 'mixed'
 lines=['当前主题：'+THEMES[theme],'','站在画面中央，让肩、肘、腕、髋入镜。',
        '按动作方向自然完成，不必保持起点或终点；识别成功即落墨，放下双手后可再做。',
        '同一轮动作最多落墨一次；识别依据运动方向、幅度和轨迹，单独摆姿势不会落墨。',
        'A01 起点：双腕放在胸前，间距约半个肩宽；直接向两侧展开，不必等待候选提示。',
        'A06 是双手向上托，A01 是向两侧展开；不必停在终点。','']
 lines += [a+'  '+ACTIONS[a]+' → '+RESULTS[theme][a] for a in ACTIONS if a in enabled]
 lines += ['','慢处较粗较浓，快处较细较淡；动作幅度和轨迹改变图案。',
           '混合主题建议：A01 画山 → A02 画竹 → A06 添叶 → A03 画梅 → A06 添花。',
           '切换主题保留已有画面；墨竹、梅枝先画枝干，再添叶、添花。',
           '完成后点“保存”，PNG 与事件 JSON 保存在板卡 works 目录。',
           '本版为单人身体创作；横扫 A05、书法和双人模式暂未开放。']
 return '\n'.join(lines)

def stage_text(phase):
 return {'idle':'自然做动作','prepare':'捕捉动作起势','ready':'继续动作，无需等待',
         'execute':'正在识别动作','hold':'已落墨，放下双手后可再做','return':'双手放下，准备下一笔',
         'unknown':'调整站位，让关节清晰入镜','lost':'等待识别恢复'}.get(phase,'等待动作')
