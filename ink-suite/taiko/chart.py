"""Original 72-second score: 青岚鼓引, composed specifically for this project."""
BPM = 100
BEAT = 60 / BPM
DURATION = 72.
TITLE = '青岚鼓引'
COMPOSER = 'InkSuite procedural original · 2026-09-27'
DIFFICULTIES = {'easy': '简单', 'normal': '普通', 'hard': '困难'}
PRACTICE = {'full': (0., 72.), 'intro': (0., 12.),
            'alternate': (28.8, 40.8), 'finale': (57.6, 72.)}
# Replace existing isolated notes, keeping every music time and note count.
# The first ten seconds remain left/right practice; claps have >=1.2s recovery
# on both sides, including the denser hard chart. B remains a double downstroke.
CLAP_TIMES = {
    'easy': (10.8, 15.6, 32.4, 61.2),
    'normal': (10.8, 15.6, 51.6, 61.2),
    'hard': (10.8, 42., 50.4, 68.4),
}


def practice_window(practice):
    if practice not in PRACTICE:
        raise ValueError('Unknown practice section')
    start, end = PRACTICE[practice]
    lead_in = start + 2.4 if practice in ('alternate', 'finale') else 2.4
    return start, end, lead_in


def chart(difficulty='easy'):
    if difficulty not in DIFFICULTIES:
        raise ValueError('Unknown difficulty')
    notes = []
    # 28 four-beat bars after an instrumental introduction; end with a coda.
    for bar in range(28):
        start = 2.4 + bar * 4 * BEAT
        if difficulty == 'hard' and bar >= 4:
            # Alternation raises density without demanding fast repeats from
            # either arm. Double-hit phrases keep both arms 1.2 seconds apart.
            if 17 <= bar < 24:
                pattern = [(0, 'B'), (2, 'B')]
            elif bar in (16, 27):
                pattern = [(0, 'L'), (2, 'R')]
            else:
                pattern = [(0, 'L'), (1, 'R'), (2, 'L'), (3, 'R')]
        elif bar < 4:
            pattern = [(0, 'L'), (2, 'R')]
        elif bar < 12:
            pattern = [(0, 'L'), (2, 'R')] if bar % 2 == 0 else [(0, 'R'), (2, 'B')]
        elif bar < 20:
            pattern = [(0, 'L'), (1, 'R'), (3, 'B')] if difficulty == 'normal' else [(0, 'L'), (2, 'B')]
        else:
            pattern = [(0, 'B'), (2, 'R' if bar % 2 else 'L')]
            if difficulty == 'normal' and bar % 2:
                pattern = [(0, 'B'), (1.5, 'L'), (3, 'R')]
        for beat, lane in pattern:
            at = round(start + beat * BEAT, 6)
            notes.append(dict(id=len(notes), time=at,
                              lane='C' if at in CLAP_TIMES[difficulty] else lane))
    notes.append(dict(id=len(notes), time=69.6, lane='B'))
    return notes


def section(t):
    return '开场' if t < 12 else '主段' if t < 50.4 else '高潮'
