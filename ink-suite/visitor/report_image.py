"""Native, paginated visitor score portraits. Pure Pillow; no camera/network I/O.

The caller supplies ``VisitorStore.view(token)`` or the JSON report payload.
Only an explicitly supplied, already-authorized artwork reader may read art.
Render this work on a low-priority worker, not on a game/GTK/audio thread.
Each image is bounded to 1080 x 7200 and the page iterator keeps one image live.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin

WIDTH = 1080
MAX_HEIGHT = 7200
IMAGE_STYLE_VERSION = 'ink-report-v6-kart-20261018'
LANCZOS = getattr(Image, 'Resampling', Image).LANCZOS
DEFAULT_ROUNDS_PER_PAGE = 20
MAX_ROUNDS_PER_PAGE = 20
GAME_ORDER = ('sword', 'fight', 'fruit', 'taiko', 'platformer', 'runner', 'kart')
# Score games use paired rows; ink remains the artwork section.
# Use the same geometry in rendering and conservative first-page planning.
OVERVIEW_HEIGHT = 79 + 146 * ((len(GAME_ORDER) + 1) // 2)
GAMES = {'sword': '御剑山河', 'fight': '格斗', 'fruit': '水果忍者',
         'taiko': '鼓动山河', 'koi': '鲤跃龙门', 'platformer':'超级玛丽', 'runner':'地铁跑酷','kart':'赛车'}
ACCENTS = {'sword': '#315f66', 'fight': '#a7473c', 'fruit': '#587142',
           'taiko': '#9b662c', 'koi': '#7d617e', 'platformer':'#448d7c', 'runner':'#b46d3e','kart':'#386d91'}
INK = '#223d3b'
MUTED = '#6c7566'
RED = '#a8473c'
GOLD = '#ab8750'
STATUS = {'active': '进行中', 'completed': '已完成', 'interrupted': '中断 / 提前离开'}
RESULT = {'win': '胜', 'won': '胜', 'lose': '负', 'lost': '负', 'draw': '平',
          'over': '结算', 'finished': '完成', 'victory': '胜', 'defeat': '负'}
COUNT_LABELS = {'perfect': '完美', 'great': '良好', 'good': '命中', 'bad': '偏差',
                'miss': '未命中', 'excellent': '完美', 'ok': '命中'}
LOCAL_ZONE = timezone(timedelta(hours=8))


def _report(value):
    if isinstance(value, (bytes, str)):
        value = json.loads(value)
    if not isinstance(value, dict) or not isinstance(value.get('rounds'), list):
        raise ValueError('Expected a visitor JSON report with rounds')
    if any(not isinstance(r, dict) or not isinstance(r.get('data', {}), dict)
           for r in value['rounds']):
        raise ValueError('Invalid visitor round')
    return value


def _page_size(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ROUNDS_PER_PAGE:
        raise ValueError('rounds_per_page must be an integer between 1 and 20')
    return value


def report_manifest(report, rounds_per_page=DEFAULT_ROUNDS_PER_PAGE):
    """One-based height-bounded pages, preserving the store's original order.

    The round limit is a maximum. Unusually long trusted counter labels cause
    earlier page breaks; space for an optional first-page art thumbnail is
    reserved so callers get the same manifest with and without artwork bytes.
    """
    report = _report(report)
    size = _page_size(rounds_per_page)
    count = len(report['rounds'])
    probe=ImageDraw.Draw(Image.new('RGB',(1,1)))
    planned=[];offset=0
    while offset<count or not planned:
        first=not planned
        # Header, overview, maximum art (380+97), section heading and footer.
        used=615+OVERVIEW_HEIGHT+477+76+213 if first else 828
        end=offset
        while end<count and end-offset<size:
            needed=_round_height(probe,report['rounds'][end])+20
            if used+needed>MAX_HEIGHT:
                if end==offset:raise ValueError('A report round exceeds the safe image height')
                break
            used+=needed;end+=1
        n=len(planned)+1
        planned.append(dict(page=n,filename=f'ink-report-{n:02d}.png',
                            first_round=offset+1 if count else 0,last_round=end,
                            round_ids=[r.get('id') for r in report['rounds'][offset:end]]))
        offset=end
    return dict(schema=1,width=WIDTH,max_height=MAX_HEIGHT,round_count=count,
                page_count=len(planned),rounds_per_page=size,pages=planned)


def _font_candidates(title=False):
    variable = 'INKSUITE_REPORT_TITLE_FONT' if title else 'INKSUITE_REPORT_FONT'
    explicit = os.environ.get(variable)
    if explicit:
        yield Path(explicit)
    # System fonts are preferred; no Microsoft fonts are bundled or redistributed.
    names = ('NotoSerifCJK-Regular.ttc', 'SourceHanSerifSC-Regular.otf', 'SourceHanSerifSC-Regular.ttf') if title else (
        'NotoSansCJK-Regular.ttc', 'NotoSansCJKsc-Regular.otf', 'SourceHanSansSC-Regular.otf',
        'wqy-zenhei.ttc', 'wqy-microhei.ttc')
    roots = [Path('/usr/share/fonts/opentype/noto'), Path('/usr/share/fonts/truetype/noto'),
             Path('/usr/share/fonts/truetype/wqy'), Path('/usr/local/share/fonts'),
             Path('/home/ubuntu/.local/share/fonts')]
    for root in roots:
        for name in names:
            yield root / name
    bundled=Path(__file__).with_name('fonts')
    if title:
        yield bundled/'notoserifsc-vf.ttf'
    yield bundled/'notosanssc-regular.otf'
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    windows = ('Source Han Serif SC Heavy (TrueType).ttf', 'NotoSerifSC-VF.ttf',
               'simkai.ttf', 'simsun.ttc') if title else (
                   'Noto Sans SC (TrueType).otf', 'NotoSansSC-VF.ttf', 'msyh.ttc', 'simhei.ttf')
    for name in windows:
        yield fonts / name
    # A Sans CJK font remains preferable to silent missing glyphs in the heading.
    if title:
        yield from _font_candidates(False)


def _cjk_font(path, size):
    index = 2 if path.name.startswith(('NotoSerifCJK-', 'NotoSansCJK-')) and path.suffix == '.ttc' else 0
    return ImageFont.truetype(str(path), size, index=index)


def _supports_chinese(font):
    missing = bytes(font.getmask('\U0010ffff'))
    for character in '墨境山河御剑鼓果访客战绩':
        mask = font.getmask(character)
        if not mask.getbbox() or bytes(mask) == missing:
            return False
    return True


@lru_cache(maxsize=4)
def font_path(title=False):
    for path in _font_candidates(title):
        if path.is_file():
            try:
                if _supports_chinese(_cjk_font(path, 28)):
                    return path
            except (OSError, ValueError):
                continue
    raise RuntimeError('未找到可显示中文的报告字体，请安装 Noto CJK 或设置 INKSUITE_REPORT_FONT。')


@lru_cache(maxsize=32)
def _font(size, title=False):
    font=_cjk_font(font_path(title), size)
    if title and font_path(title).name.lower()=='notoserifsc-vf.ttf':
        font.set_variation_by_axes([700])
    return font


def _text(draw, position, text, size=26, color=INK, title=False):
    draw.text(position, str(text), font=_font(size, title), fill=color, anchor='lt')


def _wrap(draw, text, size, width, title=False):
    font = _font(size, title)
    lines = []
    for paragraph in str(text).split('\n'):
        line = ''
        for char in paragraph:
            if line and draw.textlength(line+char, font=font) > width:
                lines.append(line)
                line = char
            else:
                line += char
        lines.append(line)
    return lines


def _ellipsize(draw, text, size, width, title=False):
    text = str(text)
    font = _font(size, title)
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text+'…', font=font) > width:
        text = text[:-1]
    return text+'…'


def _number(value, decimals=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    return f'{value:,.{decimals}f}'


def _date(value, short=False):
    if not value:
        return '尚在游玩'
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(LOCAL_ZONE)
        return stamp.strftime('%m/%d %H:%M' if short else '%Y.%m.%d  %H:%M')
    except (ValueError, TypeError, OverflowError):
        return str(value)[:32]


def _paper(height):
    image = Image.new('RGB', (WIDTH, height), '#f6f1e3')
    rng = random.Random(260)
    tile = Image.frombytes('P', (128, 128), bytes(rng.randrange(7) for _ in range(128*128)))
    palette = []
    for i in range(256):
        shift = (i % 7)-3
        palette.extend((246+shift, 241+shift, 227+shift))
    tile.putpalette(palette)
    tile = tile.convert('RGB')
    for y in range(0, height, 128):
        for x in range(0, WIDTH, 128):
            image.paste(tile, (x, y))
    return image


def _mountains(draw, y=360):
    # Low-contrast overlapping native brush silhouettes; no external art required.
    shapes = [((214, 221, 211), [(0,y+96),(78,y+47),(144,y+69),(225,y-20),(307,y+28),
               (402,y-64),(512,y+15),(601,y-50),(741,y+55),(851,y+16),(956,y+92),(1080,y+70),
               (1080,y+182),(0,y+182)]),
              ((190, 207, 198), [(0,y+140),(112,y+91),(203,y+139),(295,y+85),(431,y+113),
               (554,y+52),(674,y+139),(807,y+95),(969,y+145),(1080,y+107),(1080,y+197),(0,y+197)])]
    for fill, shape in shapes:
        draw.polygon(shape, fill=fill)
    draw.ellipse((798,y-206,910,y-94), fill='#c58167')
    for x, top in ((652,y-136),(700,y-152),(738,y-116)):
        draw.line([(x-13,top+5),(x,top),(x+15,top+7)], fill='#7a9187', width=3)
    for i in range(4):
        yy=y+174+i*9
        draw.line([(0,yy),(247,yy-3),(536,yy+2),(819,yy-3),(1080,yy)], fill='#f6f1e3', width=3)


def _icon(draw, game, x, y, scale=1):
    color = ACCENTS.get(game, INK)
    def p(a,b): return (x+a*scale, y+b*scale)
    if game == 'sword':
        draw.polygon([p(0,46),p(37,-28),p(45,-36),p(44,-19),p(10,51)], fill=color)
        draw.line([p(-5,33),p(24,47)], fill=GOLD, width=6)
        draw.line([p(5,40),p(-7,63)], fill=color, width=7)
    elif game == 'fight':
        for offset in (-14, 22):
            draw.rounded_rectangle((x+offset*scale,y-12*scale,x+(offset+28)*scale,y+25*scale),
                                   radius=7, fill=color)
            draw.line([p(offset+12,25),p(offset+8,51)], fill=color, width=10)
        draw.arc((x-33*scale,y-28*scale,x+65*scale,y+73*scale),205,340, fill=GOLD,width=3)
    elif game == 'fruit':
        draw.pieslice((x-29*scale,y-23*scale,x+53*scale,y+59*scale),0,180, fill=color)
        draw.pieslice((x-22*scale,y-17*scale,x+46*scale,y+52*scale),0,180, fill='#c96c58')
        for xx in (-5,13,31):
            draw.ellipse((x+xx*scale,y+21*scale,x+(xx+4)*scale,y+29*scale), fill=INK)
        draw.line([p(-30,12),p(59,-11)], fill=GOLD, width=4)
    elif game == 'taiko':
        draw.ellipse((x-30*scale,y-20*scale,x+52*scale,y+61*scale), fill=color)
        draw.ellipse((x-20*scale,y-10*scale,x+42*scale,y+51*scale), fill='#e9c998')
        draw.line([p(-32,-32),p(17,23)], fill=INK, width=5)
        draw.line([p(61,-29),p(11,22)], fill=INK, width=5)
    elif game == 'platformer':
        # A small pixel traveller on stepped terraces, matching its native art.
        def block(a,b,c,d,fill):
            draw.rectangle((*p(a,b),*p(c,d)),fill=fill)
        block(-30,38,-9,53,color)
        block(-9,25,13,53,color)
        block(13,10,37,53,color)
        block(-9,-37,11,-23,INK)
        block(-7,-22,9,-10,'#dbb579')
        block(-12,-9,12,12,color)
        block(-15,13,-4,25,GOLD)
        block(4,13,19,23,GOLD)
        block(31,-31,42,-14,GOLD)
    elif game == 'runner':
        draw.line([p(-31,55),p(8,-27),p(48,55)],fill='#9eaea0',width=3)
        draw.line([p(8,-27),p(8,56)],fill='#9eaea0',width=3)
        draw.ellipse((x-3*scale,y-35*scale,x+18*scale,y-14*scale),fill=color)
        for a,b in (((5,-10),(-4,17)),((0,1),(-22,5)),((0,1),(23,8)),
                    ((-4,17),(-26,40)),((-4,17),(17,26)),((17,26),(31,47))):
            draw.line([p(*a),p(*b)],fill=color,width=max(1,round(8*scale)))
        draw.ellipse((x+35*scale,y-8*scale,x+49*scale,y+8*scale),outline=GOLD,width=max(1,round(3*scale)))
    elif game == 'kart':
        draw.rounded_rectangle((x-34*scale,y+4*scale,x+44*scale,y+48*scale),radius=max(1,round(9*scale)),fill=color)
        for xx in (-39,32):
            for yy in (5,34):draw.rounded_rectangle((x+xx*scale,y+yy*scale,x+(xx+13)*scale,y+(yy+20)*scale),radius=3,fill=INK)
        draw.ellipse((x-13*scale,y-30*scale,x+20*scale,y+3*scale),fill=GOLD)
        draw.line([p(4,7),p(4,38)],fill='#f1e6bf',width=max(1,round(6*scale)))
        draw.line([p(-38,-24),p(-19,-7)],fill=GOLD,width=max(1,round(4*scale)))
        draw.line([p(41,-25),p(24,-9)],fill=GOLD,width=max(1,round(4*scale)))
    else:
        draw.ellipse((x-27*scale,y-8*scale,x+42*scale,y+36*scale), fill=color)
        draw.polygon([p(42,13),p(62,-4),p(63,34)], fill=color)


def _core_stats(row):
    data = row.get('data', {})
    game = row.get('game')
    combo = data.get('best_combo', data.get('combo'))
    if game == 'sword':
        fields = [('击败敌人', data.get('kills')), ('擦弹', data.get('graze')),
                  ('最佳连击', combo), ('剩余命数', data.get('lives'))]
    elif game == 'fight':
        fields = [('命中', data.get('hits')), ('完美格挡', data.get('perfect_blocks')),
                  ('剩余体力', data.get('hp')), ('格挡', data.get('blocks'))]
    elif game == 'fruit':
        fields = [('切中水果', data.get('fruits_cut')), ('最佳连击', combo),
                  ('命中炸弹', data.get('bombs_hit')), ('奖励得分', data.get('bonus_score'))]
    elif game == 'platformer':
        fields = [('金币', data.get('coins')), ('通关数', data.get('completed_stages')),
                  ('击败敌人', data.get('enemies_defeated')), ('剩余生命', data.get('lives'))]
    elif game == 'runner':
        fields = [('奔跑距离（米）', data.get('distance')), ('金币', data.get('coins')),
                  ('跳跃', (data.get('counts') or {}).get('jump')), ('滑铲', (data.get('counts') or {}).get('slide'))]
    elif game == 'kart':
        fields = [('最终名次',data.get('position')),('完赛用时（秒）',data.get('finish_time')),
                  ('漂移次数',data.get('drifts')),('使用道具',data.get('items_used'))]
    elif game == 'taiko':
        rank = data.get('rank', data.get('grade', '—'))
        accuracy = data.get('accuracy')
        percent = _number(accuracy, 1)+'%' if isinstance(accuracy, (int,float)) and math.isfinite(accuracy) else '—'
        return [('评价', str(rank)), ('准确率', percent), ('最佳连击', _number(combo)),
                ('有效击鼓', _number(data.get('strikes')))]
    else:
        fields = [('穿环', data.get('rings')), ('灵珠', data.get('pearls')),
                  ('最佳连击', combo), ('剩余命数', data.get('lives'))]
    return [(label, _number(value)) for label, value in fields]


def _detail(row):
    data = row.get('data', {})
    pieces = []
    if row.get('game') == 'taiko':
        counts = data.get('counts', {})
        pieces.extend(COUNT_LABELS.get(str(k).lower(), str(k))+' '+_number(v)
                      for k,v in counts.items())
    if data.get('grade') and row.get('game') != 'taiko':
        pieces.append('评价 '+str(data['grade']))
    if row.get('game') == 'fight' and data.get('enemy_hp') is not None:
        pieces.append('对手体力 '+_number(data['enemy_hp']))
    if row.get('game') == 'fruit' and data.get('missed') is not None:
        pieces.append('漏切 '+_number(data['missed']))
    if row.get('game') == 'platformer':
        outcome={'won':'三关通关','over':'命数用尽'}.get(data.get('result'))
        if outcome:pieces.append(outcome)
        if data.get('deaths') is not None:pieces.append('失误 '+_number(data['deaths'])+' 次')
        if data.get('pipes_used') is not None:pieces.append('进入管道 '+_number(data['pipes_used'])+' 次')
    if row.get('game') == 'runner':
        if data.get('result')=='over':pieces.append('本次奔跑已结束')
        if data.get('distance') is not None:pieces.append('行进 '+_number(data['distance'],1)+' 米')
    if row.get('game') == 'kart':
        if data.get('result')=='finished':pieces.append('比赛完成')
        if data.get('lap') is not None:
            pieces.append(('完成 '+_number(data['lap'])+' 圈') if data.get('result')=='finished'
                          else ('当前第 '+_number(data['lap'])+' 圈'))
        if data.get('best_lap') is not None:pieces.append('最快单圈 '+_number(data['best_lap'],2)+' 秒')
    duration = data.get('elapsed', data.get('time', data.get('duration')))
    if duration is not None:
        pieces.append('游玩 '+_number(duration,1)+' 秒')
    if data.get('end_reason'):
        reasons = {'lost':'命数用尽','won':'通关','timeout':'时间结束','health':'体力用尽',
                   'tracking_lost':'追踪中断','finished':'完成'}
        pieces.append(reasons.get(str(data['end_reason']), str(data['end_reason'])))
    return '  ·  '.join(pieces)


def _round_height(draw, row):
    # Long trusted counter labels are wrapped, not silently dropped.
    return 246 + 27*max(0, len(_wrap(draw, _detail(row), 19, 900))-1)


def _draw_round(draw, row, number, y):
    height = _round_height(draw, row)
    game = row.get('game', '')
    color = ACCENTS.get(game, INK)
    draw.rounded_rectangle((48,y+5,1032,y+height+5), radius=21, fill='#e4ddcb')
    draw.rounded_rectangle((48,y,1032,y+height), radius=21, fill='#fffcf3')
    draw.rounded_rectangle((48,y,56,y+height), radius=4, fill=color)
    _text(draw, (77,y+22), f'{number:02d}', 28, color)
    _text(draw, (136,y+20), row.get('title') or GAMES.get(game,game), 31, INK, True)
    status = STATUS.get(row.get('status'), str(row.get('status','')))
    pill_width = max(110, int(draw.textlength(status,font=_font(20)))+30)
    draw.rounded_rectangle((990-pill_width,y+22,990,y+57), radius=12,
                           fill='#e7efe7' if row.get('status')=='completed' else '#efe5d9')
    _text(draw,(1005-pill_width,y+27),status,20,color)
    rules = str(row.get('rules_label') or row.get('rules') or '标准玩法')
    _text(draw,(136,y+64),_ellipsize(draw,rules,20,610),20,MUTED)
    stamp = _date(row.get('ended') or row.get('started'), short=True)
    _text(draw,(801,y+66),stamp,18,MUTED)
    primary = _number(row.get('score'))
    primary_label = '得分'
    if game == 'fight':
        raw_result = str(row.get('data',{}).get('result',''))
        primary = RESULT.get(raw_result,raw_result) or '—'
        primary_label = '对决结果  ·  排名分 '+_number(row.get('score'))
    if game == 'taiko':
        primary_label = '节奏得分'
    score_size = 59 if len(primary)<=10 else 43
    while score_size>26 and draw.textlength(primary,font=_font(score_size,True))>340:
        score_size-=1
    primary=_ellipsize(draw,primary,score_size,340,True)
    _text(draw,(78,y+109),primary,score_size,color,True)
    _text(draw,(80,y+179),_ellipsize(draw,primary_label,19,335),19,MUTED)
    for i,(label,value) in enumerate(_core_stats(row)):
        x = 462+(i%2)*260
        top = y+105+(i//2)*62
        _text(draw,(x,top),label,19,MUTED)
        _text(draw,(x+132,top-4),_ellipsize(draw,value,27,112),27,color,True)
    for i,line in enumerate(_wrap(draw,_detail(row),19,900)):
        _text(draw,(79,y+214+i*27),line,19,MUTED)
    return height


def _header(draw, report, page, pages, compact=False):
    _mountains(draw, 210 if compact else 300)
    _text(draw,(61,54),'游玩报告',78,INK,True)
    _text(draw,(65,145),'IN K  ·  MOUNTAINS  ·  MEMORIES'.replace('IN K','INK'),18,MUTED)
    draw.rounded_rectangle((912,55,991,144),radius=7,outline=RED,width=3)
    _text(draw,(925,66),'墨境',26,RED,True)
    _text(draw,(925,106),'成绩',26,RED,True)
    name = str(report.get('name') or '游客')
    size = 42
    while size>27 and draw.textlength(name+' 的游玩战绩',font=_font(size,True))>930:
        size -= 1
    _text(draw,(65,211),name+' 的游玩战绩',size,INK,True)
    _text(draw,(66,275),'开始 '+_date(report.get('started'))+'   ·   '+
          ('已归档' if report.get('status')=='archived' else '本次游玩中'),23,MUTED)
    _text(draw,(66,317),'结束 '+_date(report.get('ended'))+'   /   第 '+str(page)+' 页 · 共 '+str(pages)+' 页',22,MUTED)
    y = 392 if compact else 468
    rounds = report['rounds']
    completed = sum(r.get('status')=='completed' for r in rounds)
    interrupted = sum(r.get('status')=='interrupted' for r in rounds)
    values = [(completed,'完成回合'),(interrupted,'中断回合'),
              (len({r.get('game') for r in rounds}),'游戏足迹'),(len(report.get('artworks',[])),'水墨作品')]
    draw.rounded_rectangle((49,y,1031,y+111),radius=19,fill='#fffcf3')
    for i,(value,label) in enumerate(values):
        x=79+i*246
        if i:draw.line((x-15,y+23,x-15,y+89),fill='#e4dcc7',width=1)
        _text(draw,(x,y+15),str(value),42,INK,True)
        _text(draw,(x+81,y+36),label,21,MUTED)
    return y+147


def _overview(draw, report, y):
    _text(draw,(61,y),'最佳成绩',34,INK,True)
    _text(draw,(251,y+10),'完成局的最佳成绩 · 不混合不同游戏',20,MUTED)
    y+=63
    for index,game in enumerate(GAME_ORDER):
        x=49+(index%2)*507
        top=y+(index//2)*146
        rows=[r for r in report['rounds'] if r.get('game')==game]
        finished=[r for r in rows if r.get('status')=='completed']
        draw.rounded_rectangle((x,top,x+475,top+126),radius=16,fill='#eceadb')
        _icon(draw,game,x+52,top+47,.78)
        _text(draw,(x+120,top+18),GAMES[game],27,ACCENTS[game],True)
        best=max((r.get('score',0) for r in finished),default=None)
        label=('最佳排名分 ' if game=='fight' else '最佳得分 ')+_number(best) if finished else '尚无完成局'
        _text(draw,(x+120,top+56),label,23,INK)
        _text(draw,(x+120,top+91),'完成 '+str(len(finished))+' 局 / 总共 '+str(len(rows))+' 局',18,MUTED)
    return y+146*((len(GAME_ORDER)+1)//2)+16


def _read_artwork(report, reader):
    if reader is None or not report.get('artworks'):
        return None
    ident=report['artworks'][0].get('id')
    if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('Invalid artwork identity')
    # The reader must be backed by VisitorStore.artwork(token, ident). No path input.
    data=reader(ident)
    if not isinstance(data,bytes) or not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>30_000_000:
        raise ValueError('Artwork reader must return bounded PNG bytes')
    with Image.open(io.BytesIO(data)) as artwork:
        if artwork.format!='PNG' or artwork.width*artwork.height>16_000_000:
            raise ValueError('Artwork image is too large')
        artwork.load()
        return ImageOps.contain(artwork.convert('RGB'),(902,380),LANCZOS)


def render_report_page(report, page=1, artwork_reader=None,
                       rounds_per_page=DEFAULT_ROUNDS_PER_PAGE):
    """Return one PNG, one-based page. No database, filesystem art or network reads.

    The optional reader receives only the first art ID and returns authorized PNG
    bytes; art is placed only on page one. There is no truncation of round records.
    Lower ``rounds_per_page`` if unusual counter strings need extra vertical room.
    """
    report=_report(report)
    manifest=report_manifest(report,rounds_per_page)
    if isinstance(page,bool) or not isinstance(page,int) or not 1<=page<=manifest['page_count']:
        raise ValueError('Report page does not exist')
    # Force valid CJK fonts before allocating a large image.
    font_path();font_path(True)
    item=manifest['pages'][page-1]
    start=max(0,item['first_round']-1)
    rounds=report['rounds'][start:item['last_round']]
    art=_read_artwork(report,artwork_reader) if page==1 else None
    probe=ImageDraw.Draw(Image.new('RGB',(1,1)))
    first=page==1
    header_bottom=615 if first else 539
    overview_height=OVERVIEW_HEIGHT if first else 0
    art_height=art.height+97 if art else 0
    round_heights=[_round_height(probe,r) for r in rounds]
    content_top=header_bottom+overview_height+art_height+76
    height=content_top+sum(h+20 for h in round_heights)+213
    if not rounds:height+=184
    if height>MAX_HEIGHT:
        raise ValueError('Report page exceeds height limit; reduce rounds_per_page')
    image=_paper(height)
    draw=ImageDraw.Draw(image)
    y=_header(draw,report,page,manifest['page_count'],compact=not first)
    if first:y=_overview(draw,report,y)
    if art:
        _text(draw,(61,y+9),'水墨留影',30,INK,True)
        image.paste(art,((WIDTH-art.width)//2,y+65))
        y+=art.height+97
    _text(draw,(61,y+5),'逐局战绩' if rounds else '暂无成绩',33,INK,True)
    right='本页 '+str(len(rounds))+' 局 / 全部 '+str(len(report['rounds']))+' 局'
    _text(draw,(735,y+14),right,20,MUTED)
    y+=76
    if not rounds:
        draw.rounded_rectangle((49,y,1031,y+162),radius=22,fill='#fffcf3')
        _text(draw,(81,y+35),'还没有游戏回合记录',31,INK,True)
        _text(draw,(81,y+87),'挥剑、格斗、切水果、击鼓、闯关或跑酷，留下你的成绩。',23,MUTED)
        y+=184
    else:
        for i,row in enumerate(rounds):
            y+=_draw_round(draw,row,start+i+1,y)+20
    draw.line((61,y+27,1019,y+27),fill='#c5b590',width=2)
    _text(draw,(61,y+52),'感谢游玩，可以保存这份报告。',29,INK,True)
    _text(draw,(62,y+101),'中断与进行中回合保留记录，不参与排行榜。各玩法独立分榜。',19,MUTED)
    _text(draw,(62,y+136),'墨境 · KV260  /  '+str(page)+' of '+str(manifest['page_count']),19,GOLD)
    info=PngImagePlugin.PngInfo()
    info.add_text('InkSuiteReport',json.dumps(dict(schema=1,style=IMAGE_STYLE_VERSION,page=page,page_count=manifest['page_count'],
        round_ids=manifest['pages'][page-1]['round_ids'],round_count=len(report['rounds'])),ensure_ascii=False))
    info.add_text('Title','游玩报告 · '+str(report.get('name') or '游客'))
    output=io.BytesIO()
    image.save(output,format='PNG',compress_level=3,pnginfo=info)
    image.close()
    return output.getvalue()


def render_report_pages(report, artwork_reader=None, rounds_per_page=DEFAULT_ROUNDS_PER_PAGE):
    """Yield (filename, PNG) one image at a time for the complete ZIP download."""
    report=_report(report)
    manifest=report_manifest(report,rounds_per_page)
    for item in manifest['pages']:
        yield item['filename'],render_report_page(report,item['page'],artwork_reader,rounds_per_page)


def report_fingerprint(report):
    """Cache key includes name, dates, art metadata and every round/counter."""
    data=json.dumps(dict(style=IMAGE_STYLE_VERSION,report=_report(report)),ensure_ascii=False,
                    sort_keys=True,allow_nan=False,separators=(',',':')).encode()
    return hashlib.sha256(data).hexdigest()
