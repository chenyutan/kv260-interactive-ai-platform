"""Small, renderer-independent adapters for *accepted* gameplay events.

``AudioEvents(mode, director.play)`` never reads poses, advances a game, drains
its visual events, or controls a clock. Call ``observe(game)`` once to establish
a silent baseline, then after gameplay updates. Fruit cuts have no engine ID:
pass each event to ``fruit_cut(event, key=cut_sequence)`` inside the existing
visual consumer. Ink save/clear use successful command acknowledgements.

Observe with ``enabled=False`` before pausing/resuming or reconnecting to absorb
old outcomes without playing them. Call ``reset()`` and silently observe after
an explicit restart. Cooldowns, volume and asynchronous playback belong to the
audio runtime, not to this adapter. The callback must be a nonblocking enqueue.
"""
from collections import OrderedDict


MODES = frozenset(('ink', 'fruit', 'fight', 'taiko', 'sword'))
FIGHT_MOVES = frozenset(('left', 'right', 'uppercut_left', 'uppercut_right',
                         'palm', 'special', 'kick_left', 'kick_right'))
SWORD_EVENTS = ('shot', 'hit', 'kill', 'hurt', 'collect', 'special', 'boss', 'win', 'lose')


def _get(source, field, default=None):
    return source.get(field, default) if isinstance(source, dict) else getattr(source, field, default)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class AudioEvents:
    """Bounded event mapping. Gameplay remains authoritative if audio fails.

    ``observe`` accepts a live Fight/Fruit/sword Game, a live taiko Game or its
    snapshot, or an ink /api/state dictionary. Fight effects are live objects;
    pass the game itself, not freshly copied effect dictionaries every frame.
    All output keys include a session generation and remain stable on retries.
    """
    def __init__(self, mode, emit, history_limit=256):
        if mode not in MODES:
            raise ValueError('Unknown audio event mode: ' + str(mode))
        if not callable(emit):
            raise TypeError('emit must be a nonblocking callable')
        if not isinstance(history_limit, int) or history_limit < 16:
            raise ValueError('history_limit must be at least 16')
        self.mode, self.emit, self.history_limit = mode, emit, history_limit
        self.generation = 0
        self.callback_errors = 0
        self._clear()

    def _clear(self):
        self._seen = OrderedDict()
        self._effects = OrderedDict()
        self._effect_sequence = 0
        self._previous = None
        self._source = None

    def reset(self):
        """Begin a new round; the next observation only establishes a baseline."""
        self.generation += 1
        self._clear()

    def _once(self, sound, identity, enabled):
        key = (self.mode, self.generation, sound, identity)
        if key in self._seen:
            return False
        self._seen[key] = None
        while len(self._seen) > self.history_limit:
            self._seen.popitem(last=False)
        if not enabled:
            return False
        try:
            return self.emit(sound, key=key) is not False
        except Exception:
            # A device/backend error must not interrupt scoring or presentation.
            self.callback_errors += 1
            return False

    def fruit_cut(self, event, *, key, enabled=True):
        """Copy nothing and consume nothing; caller supplies its stable cut ID."""
        if self.mode != 'fruit' or not isinstance(event, dict):
            return
        kind = event.get('kind')
        sound = {'normal': 'fruit_slice', 'double': 'fruit_double',
                 'ice': 'fruit_freeze', 'bomb': 'fruit_bomb'}.get(kind)
        if sound is None:
            return
        self._once(sound, ('cut', key), enabled)
        if kind != 'bomb' and _count(event.get('combo')) >= 3:
            self._once('fruit_combo', ('cut', key), enabled)

    def command(self, name, accepted, *, key, enabled=True):
        """Only a successful ink command response acknowledges save or clear."""
        if self.mode != 'ink' or not accepted:
            return
        sound = {'save': 'ink_save', 'clear': 'ink_clear',
                 'new_scroll': 'ink_clear'}.get(name)
        if sound:
            self._once(sound, ('command', key), enabled)

    def observe(self, source, *, enabled=True):
        """Observe accepted outcomes without mutating the source or its queues."""
        if source is None:
            # Explicit transport loss: the next packet is a silent baseline.
            self._previous = None
            return
        if self.mode == 'ink':
            self._ink(source, enabled)
            return
        if not isinstance(source, dict):
            if self._source is not None and self._source is not source:
                self.reset()
            self._source = source
        getattr(self, '_' + self.mode)(source, enabled)

    def _ink(self, source, enabled):
        if _get(source, 'stale', False) or _get(source, 'available') is False:
            self._previous = None
            return
        current = dict(pid=_get(source, 'pid'), count=_count(_get(source, 'event_count')),
                       revision=_get(source, 'art_revision'))
        previous = self._previous
        if previous and current['pid'] != previous['pid']:
            self.reset()
            previous = None
        self._previous = current
        if previous and current['count'] > previous['count']:
            self._once('ink_stroke', (current['revision'], current['count']), enabled)

    def _fruit(self, game, enabled):
        current = dict(time=_get(game, 'elapsed', 0), missed=_count(_get(game, 'missed')))
        previous = self._previous
        if previous and (current['time'] < previous['time'] or current['missed'] < previous['missed']):
            self.reset()
            previous = None
        self._previous = current
        if previous and current['missed'] > previous['missed']:
            self._once('fruit_miss', current['missed'], enabled and _get(game, 'state') in ('playing', 'over'))

    def _fight(self, game, enabled):
        counts = {move: _count((_get(game, 'move_counts', {}) or {}).get(move)) for move in FIGHT_MOVES}
        current = dict(time=_get(game, 'time', 0), moves=counts, state=_get(game, 'state'))
        previous = self._previous
        if previous and (current['time'] < previous['time'] or
                         any(counts[m] < previous['moves'][m] for m in FIGHT_MOVES)):
            self.reset()
            previous = None
        live = bool(previous) and enabled and current['state'] in ('playing', 'finishing', 'over')
        if previous:
            for move in sorted(FIGHT_MOVES):
                if counts[move] > previous['moves'][move]:
                    sound = 'fight_special' if move == 'special' else 'fight_kick' if move.startswith('kick_') else 'fight_swing'
                    self._once(sound, ('move', move, counts[move]), live)
        # Effects stay alive for several frames and their age changes in place.
        # Retain bounded references so Python cannot reuse a remembered object ID.
        for effect in list(_get(game, 'effects', ()))[-6:]:
            identity = id(effect)
            if identity in self._effects:
                continue
            self._effects[identity] = effect
            while len(self._effects) > self.history_limit:
                self._effects.popitem(last=False)
            self._effect_sequence += 1
            kind = effect.get('kind')
            if kind in ('hit', 'block'):
                self._once('fight_' + kind, ('impact', self._effect_sequence), live)
        if previous and current['state'] == 'over' and previous['state'] != 'over':
            sound = {'胜': 'fight_win', '负': 'fight_lose'}.get(_get(game, 'result'))
            if sound:
                self._once(sound, 'result', live)
        self._previous = current

    def _taiko(self, game, enabled):
        counts = _get(game, 'counts', {}) or {}
        strikes=_get(game,'strike_feedback',None)
        strike_rows=list(strikes)[-64:] if strikes is not None else []
        current = dict(time=_get(game, 'time', 0), judged=sum(_count(counts.get(g)) for g in ('Perfect', 'Good', 'OK', 'Miss')),
                       finished=bool(_get(game, 'finished', False)),
                       strike_serial=max((_count(e.get('serial')) for e in strike_rows),default=0))
        previous = self._previous
        if previous and (current['time'] < previous['time'] or current['judged'] < previous['judged']
                         or current['strike_serial'] < previous['strike_serial']):
            self.reset()
            previous = None
        live = bool(previous) and enabled and (not _get(game, 'paused', True) or current['finished'])
        if strikes is not None:
            for event in strike_rows:
                if not previous or _count(event.get('serial'))<=previous['strike_serial']:continue
                sound={'L':'taiko_left','R':'taiko_right','C':'taiko_both'}.get(event.get('hand'))
                if sound is not None and event.get('serial') is not None:
                    self._once(sound,('strike',event['serial']),live)
        # Feedback is populated only by judgement, never by reading chart notes.
        for event in list(_get(game, 'feedback', ()))[-12:]:
            grade, lane, note_id = event.get('grade'), event.get('lane'), event.get('note_id')
            if grade == 'Miss':
                sound = 'taiko_miss'
            elif strikes is None and grade in ('Perfect', 'Good', 'OK'):
                sound = {'L': 'taiko_left', 'R': 'taiko_right', 'B': 'taiko_both', 'C': 'taiko_both'}.get(lane)
            else:
                sound = None
            if sound is not None and note_id is not None:
                self._once(sound, ('note', note_id, grade), live)
        if current['finished']:
            self._once('taiko_finish', 'result', live and not previous['finished'] if previous else False)
        self._previous = current

    def _sword(self, game, enabled):
        # The engine counts accepted outcomes independently of rendering and
        # playback. Never inspect or consume individual swords/effects here.
        source = _get(game, 'audio_counts', {})
        counts = {name: _count(source.get(name)) for name in SWORD_EVENTS}
        current = dict(round=_get(game, 'audio_round'), time=_get(game, 'time', 0),
                       counts=counts)
        previous = self._previous
        if previous and (current['round'] != previous['round'] or
                         current['time'] < previous['time'] or
                         any(counts[name] < previous['counts'][name] for name in SWORD_EVENTS)):
            self.reset()
            previous = None
        live = (bool(previous) and enabled and not _get(game, 'paused', True) and
                _get(game, 'tracking', False) and _get(game, 'phase') in ('playing', 'won', 'lost'))
        if previous:
            # One cue per changed category, even when a slow GUI tick contains
            # many simulation steps, hits or kills. Runtime applies cooldowns.
            for name in SWORD_EVENTS:
                if counts[name] > previous['counts'][name]:
                    self._once('sword_' + name, ('round', current['round'], counts[name]), live)
        self._previous = current
