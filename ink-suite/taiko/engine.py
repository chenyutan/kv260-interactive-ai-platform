"""Deterministic rhythm judgement against one externally supplied audio clock.

Camera compensation is done by Session. Scoring, phrase bonuses and fever timers
never depend on rendering rate or wall-clock time.
"""
import math
from collections import deque
from .chart import chart, DURATION, DIFFICULTIES

PERFECT = .110
GOOD = .220
SIMULTANEOUS = .120
INPUT_GRACE = .450  # Transport settlement grace, never a wider hit window.
FEVER_DURATION = 4.8
# Hit tolerance is a gameplay choice, independent of camera age/transport.
# The legacy constants above continue to describe the strict contract.
JUDGMENTS = {
    'strict': dict(label='严格节奏', perfect=PERFECT, good=GOOD,
                   outer=GOOD, simultaneous=SIMULTANEOUS),
    'relaxed': dict(label='体感友好', perfect=PERFECT, good=GOOD,
                    outer=.350, simultaneous=.220),
}
GRADE_RULES = {
    'Perfect': dict(points=1000, groove=8, accuracy=1.),
    'Good': dict(points=650, groove=5, accuracy=.65),
    'OK': dict(points=350, groove=3, accuracy=.35),
    'Miss': dict(points=0, groove=-24, accuracy=0.),
}
# A clap is one independent gesture. Only left/right downstrokes can fill B.
HAND_LANES = {'L': ('L', 'B'), 'R': ('R', 'B'), 'C': ('C',)}


def judgment_rules(profile):
    if not isinstance(profile, str) or profile not in JUDGMENTS:
        raise ValueError('Unknown judgment profile')
    return dict(JUDGMENTS[profile])


CHALLENGES = (
    ('alternate', '交替击鼓', 31.2, 40.8, None, 1200),
    ('together', '双手合击', 43.2, 50.4, 'B', 1600),
    ('finale', '结束段', 60., 70., None, 2400),
)


class Game:
    def __init__(self, difficulty='easy', notes=None, duration=DURATION,
                 input_grace=INPUT_GRACE, start_time=0., practice='full',
                 lead_in_until=2.4, judgment='strict'):
        if difficulty not in DIFFICULTIES:
            raise ValueError('Unknown difficulty')
        self.difficulty = difficulty
        self.judgment = judgment
        self.judgment_rules = judgment_rules(judgment)
        self.start_time = float(start_time)
        self.duration = float(duration)
        if (not math.isfinite(self.start_time) or not math.isfinite(self.duration)
                or self.start_time < 0 or self.duration <= self.start_time):
            raise ValueError('Invalid game interval')
        self.practice = practice
        self.practice_end = self.duration
        if not math.isfinite(float(lead_in_until)):
            raise ValueError('Invalid lead in')
        self.lead_in_until = min(self.duration, max(self.start_time, float(lead_in_until)))
        self.notes = [dict(n, status='pending', hits={})
                      for n in (chart(difficulty) if notes is None else notes)]
        if any(not math.isfinite(n['time']) or n['lane'] not in ('L', 'R', 'B', 'C')
               or n['time'] < self.start_time or n['time'] >= self.duration for n in self.notes):
            raise ValueError('Invalid chart')
        if len({n['id'] for n in self.notes}) != len(self.notes):
            raise ValueError('Duplicate note id')
        self.notes.sort(key=lambda n: n['time'])
        if not math.isfinite(input_grace) or input_grace < 0:
            raise ValueError('Invalid input grace')
        self.input_grace = input_grace
        self.time = self.start_time
        self.paused = True
        self.finished = False
        self.score = self.combo = self.best_combo = 0
        self.counts = dict(Perfect=0, Good=0, Miss=0)
        if self.judgment == 'relaxed':
            self.counts['OK'] = 0
        self.feedback = deque(maxlen=12)
        self.strike_feedback = deque(maxlen=64)
        self.last_strike_diagnostic = None
        self.seen = set()
        self.seen_order = deque()
        self.strikes = self.empty_strikes = 0
        self.groove = 0
        self.fever_until = self.start_time
        self.bonus_score = 0
        self.challenges = []
        self._challenge_for_note = {}
        for ident, label, start, end, lane, reward in CHALLENGES:
            # Award complete phrases, never a clipped tail of one.
            if start < self.start_time or end > self.duration:
                continue
            ids = {n['id'] for n in self.notes if start <= n['time'] < end
                   and (lane is None or n['lane'] == lane)}
            if not ids:
                continue
            challenge = dict(id=ident, label=label, start=start, end=end,
                             progress=0, target=len(ids), status='pending',
                             reward=reward, earned=0)
            self.challenges.append(challenge)
            for note_id in ids:
                self._challenge_for_note[note_id] = challenge

    def pause(self, value=True):
        self.paused = bool(value)
        # A half double-strike cannot survive menu/loss and fire on return.
        if self.paused:
            for note in self.notes:
                if note['status'] == 'pending':
                    note['hits'].clear()

    def _judge(self, note, grade, at, display_time=None):
        if note['status'] != 'pending':
            return
        note['status'] = grade
        self.counts[grade] += 1
        # Use capture-compensated music time, not the UI delivery frame.
        judge_time = at
        active_fever = judge_time < self.fever_until - 1e-9
        multiplier = 2 if active_fever else 1
        points = 0
        if grade == 'Miss':
            self.combo = 0
            self.groove = max(0, self.groove + GRADE_RULES[grade]['groove'])
        else:
            self.combo += 1
            self.best_combo = max(self.best_combo, self.combo)
            base = GRADE_RULES[grade]['points'] + min(200, (self.combo // 10) * 20)
            points = base * multiplier
            self.score += points
            if not active_fever:
                self.groove = min(100, self.groove + GRADE_RULES[grade]['groove'])
                if self.groove >= 100:
                    self.fever_until = judge_time + FEVER_DURATION
                    self.groove = 0
        challenge = self._challenge_for_note.get(note['id'])
        if challenge is not None and challenge['status'] not in ('failed', 'complete'):
            challenge['status'] = 'active'
            if grade == 'Miss':
                challenge['status'] = 'failed'
            else:
                challenge['progress'] += 1
                if challenge['progress'] == challenge['target']:
                    challenge['status'] = 'complete'
                    challenge['earned'] = challenge['reward']
                    self.bonus_score += challenge['reward']
                    self.score += challenge['reward']
        # Signed worst hand describes the error that determined a double hit's
        # grade. Miss has no invented timing value for an absent hand.
        timing_ms = None if grade == 'Miss' else 1000 * max(
            (t - note['time'] for t in note['hits'].values()), key=abs)
        timing = ('miss' if timing_ms is None else 'on_time' if abs(timing_ms) <= 35
                  else 'early' if timing_ms < 0 else 'late')
        self.feedback.append(dict(note_id=note['id'], lane=note['lane'], grade=grade,
                                  time=at, combo=self.combo, timing_ms=timing_ms,
                                  timing=timing, multiplier=multiplier, points=points,
                                  display_time=max(self.time, at) if display_time is None else display_time))

    def advance(self, at):
        if self.paused or self.finished or not math.isfinite(at):
            return
        if at < self.time - .002:
            raise ValueError('Clock rewind requires a new Game')
        self.time = max(self.time, min(self.duration, at))
        for challenge in self.challenges:
            if challenge['status'] == 'pending' and self.time >= challenge['start']:
                challenge['status'] = 'active'
        for note in self.notes:
            if note['status'] == 'pending' and (self.time > note['time'] + self.judgment_rules['outer'] + self.input_grace
                                              or self.time >= self.duration):
                self._judge(note, 'Miss', self.time)
        if self.time >= self.duration:
            self.finished = True

    def nearest_note(self, hand, at):
        """Diagnostic context only; this never changes note selection or scoring."""
        def describe(note):
            return dict(id=note['id'], lane=note['lane'], time=note['time'],
                        status=note['status'], delta_ms=(at-note['time'])*1000,
                        hand_already_hit=hand in note['hits']) if note else None
        nearest=lambda notes: min(notes, key=lambda n:(abs(at-n['time']),n['time']), default=None)
        return dict(nearest=describe(nearest(self.notes)),
                    nearest_same_hand=describe(nearest([n for n in self.notes if n['lane'] in HAND_LANES.get(hand, ())])))

    def strike(self, hand, at, event_id, *, display_time=None):
        if self.paused or self.finished or hand not in ('L', 'R', 'C') or not math.isfinite(at):
            return None
        key = (hand, str(event_id))
        if key in self.seen:
            return None
        self.seen.add(key)
        self.seen_order.append(key)
        if len(self.seen_order) > 512:
            self.seen.discard(self.seen_order.popleft())
        self.strikes += 1
        shown=max(self.time, at) if display_time is None else display_time
        pulse=dict(serial=self.strikes, hand=hand, time=at, display_time=shown)
        self.strike_feedback.append(pulse)
        context=self.nearest_note(hand, at)
        diagnostic=dict(outcome='empty', reason='no_note', **context)
        self.last_strike_diagnostic=diagnostic
        if at < self.start_time or at > self.duration:
            self.empty_strikes += 1
            diagnostic['reason']='outside_interval'
            pulse.update(outcome='empty',reason='outside_interval')
            return None
        lanes = HAND_LANES[hand]
        choices = [n for n in self.notes if n['status'] == 'pending' and n['lane'] in lanes
                   and hand not in n['hits'] and abs(at - n['time']) <= self.judgment_rules['outer'] + 1e-9]
        if not choices:
            self.empty_strikes += 1
            nearest=context['nearest']
            same=context['nearest_same_hand']
            if nearest and nearest['status']=='pending' and abs(nearest['delta_ms'])<=self.judgment_rules['outer']*1000+1e-6 and nearest['lane'] not in lanes:
                diagnostic['reason']='wrong_hand'
            elif same:
                diagnostic['reason']=('already_judged' if same['status']!='pending' else
                    'hand_already_hit' if same['hand_already_hit'] else
                    'early' if same['delta_ms']<0 else 'late')
            pulse.update(outcome='empty', reason=diagnostic['reason'])
            return None
        note = min(choices, key=lambda n: (abs(at - n['time']), n['time']))
        diagnostic.update(note_id=note['id'], note_lane=note['lane'], delta_ms=(at-note['time'])*1000)
        note['hits'][hand] = at
        if note['lane'] == 'B':
            if len(note['hits']) < 2:
                diagnostic.update(outcome='partial', reason='waiting_other_hand')
                pulse.update(outcome='partial', reason='waiting_other_hand')
                return 'partial'
            diagnostic['double_gap_ms']=abs(note['hits']['L']-note['hits']['R'])*1000
            if abs(note['hits']['L'] - note['hits']['R']) > self.judgment_rules['simultaneous'] + 1e-9:
                diagnostic.update(outcome='Miss', reason='double_hand_gap')
                pulse.update(outcome='Miss', reason='double_hand_gap')
                self._judge(note, 'Miss', at, shown)
                return 'Miss'
        error = max(abs(t - note['time']) for t in note['hits'].values())
        grade = ('Perfect' if error <= self.judgment_rules['perfect'] + 1e-9 else
                 'Good' if error <= self.judgment_rules['good'] + 1e-9 else 'OK')
        diagnostic.update(outcome=grade, reason='matched')
        pulse.update(outcome=grade, reason='matched')
        self._judge(note, grade, at, shown)
        return grade

    def snapshot(self):
        judged = sum(self.counts.values())
        accuracy = 100 * sum(GRADE_RULES[grade]['accuracy'] * count
                             for grade, count in self.counts.items()) / judged if judged else 0.
        rank = ('--' if not judged else 'S' if accuracy >= 95 else 'A' if accuracy >= 85
                else 'B' if accuracy >= 70 else 'C' if accuracy >= 50 else 'D')
        remaining = max(0., self.fever_until - self.time)
        current = next((c for c in self.challenges if c['end'] > self.time), None)
        if current is None and self.challenges:
            current = self.challenges[-1]
        return dict(time=self.time, duration=self.duration, paused=self.paused, finished=self.finished,
                    start_time=self.start_time, lead_in_until=self.lead_in_until, practice=self.practice,
                    practice_end=self.practice_end,
                    score=self.score, combo=self.combo, best_combo=self.best_combo, counts=dict(self.counts),
                    difficulty=self.difficulty, difficulty_label=DIFFICULTIES[self.difficulty],
                    judgment=self.judgment, judgment_label=self.judgment_rules['label'],
                    judgment_windows_ms=dict(Perfect=self.judgment_rules['perfect']*1000,
                        Good=self.judgment_rules['good']*1000,
                        **({'OK': self.judgment_rules['outer']*1000} if self.judgment == 'relaxed' else {})),
                    hit_window_ms=self.judgment_rules['outer']*1000,
                    simultaneous_ms=self.judgment_rules['simultaneous']*1000,
                    accuracy=accuracy, rank=rank, groove=self.groove, fever_remaining=remaining,
                    fever_active=remaining > 0, bonus_score=self.bonus_score,
                    challenge=dict(current) if current else None,
                    challenges=[dict(c) for c in self.challenges],
                    strikes=self.strikes, empty_strikes=self.empty_strikes,
                    notes=[dict(id=n['id'], time=n['time'], lane=n['lane'], status=n['status'])
                           for n in self.notes if self.time - .5 < n['time'] < self.time + 3.4],
                    feedback=list(self.feedback), strike_feedback=list(self.strike_feedback), total_notes=len(self.notes))
