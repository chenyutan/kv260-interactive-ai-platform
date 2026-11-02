"""One short recovery buffer for newly recognized moves.

Call submit for new input and flush after Fight.advance. `now` is the caller's
monotonic wall clock, never the pose timestamp. The caller must clear on lost
tracking, focus loss, or pause; this class also rejects non-playing states.
Normally only the last 160 ms of recovery can hold one input for 180 ms.
An alternating straight punch may also wait through the opposite straight
punch's remaining animation, capped at 380 ms. It never cancels that attack.
A knee-lift edge may wait up to 400 ms for a straight/uppercut punch to finish,
with a 440 ms lifetime cap. It occupies the same single slot.
"""
import math

from engine import MOVES


class InputGate:
    QUEUE_WINDOW = .16
    MAX_AGE = .18
    ALTERNATING_MAX_AGE = .38
    KICK_COMBO_WINDOW = .40
    KICK_COMBO_MAX_AGE = .44
    KICK_COMBO_PUNCHES = frozenset(('left','right','uppercut_left','uppercut_right'))

    def __init__(self):
        self.clear()

    def clear(self):
        self.pending = None

    @staticmethod
    def _valid_time(now):
        return isinstance(now, (int, float)) and math.isfinite(now)

    @staticmethod
    def _has_energy(game, move):
        # Ordinary moves earn energy on hit; only the special consumes it.
        return move != 'special' or game.energy >= game.max_energy

    @staticmethod
    def _remaining(game):
        recovery = 0.
        if game.attack:
            recovery = game.attack['duration'] - game.attack['age']
        return max(0., recovery, game.cooldown - game.time)

    def _expire(self, game, now):
        p = self.pending
        if p and (p['game'] is not game or now < p['created']
                  or now - p['created'] >= p.get('max_age',self.MAX_AGE) - 1e-9
                  or game.time < p['game_time'] - 1e-9):
            self.clear()
            return True
        return False

    def submit(self, game, move, now):
        """Return accepted, queued, busy, no_energy, inactive, or invalid."""
        if not self._valid_time(now):
            self.clear()
            return 'invalid'
        if game.state != 'playing':
            self.clear()
            return 'inactive'
        self._expire(game, now)
        if not isinstance(move, str) or move not in MOVES:
            return 'invalid'
        if not self._has_energy(game, move):
            return 'no_energy'
        if self.pending is not None:
            return 'busy'
        if game.punch(move):
            return 'accepted'
        remaining=self._remaining(game)
        max_age=self.MAX_AGE
        if remaining > self.QUEUE_WINDOW + 1e-9:
            attack=game.attack
            opposite={'left':'right','right':'left'}.get(move)
            if (attack and move in ('kick_left','kick_right')
                    and attack['move'] in self.KICK_COMBO_PUNCHES
                    and remaining <= self.KICK_COMBO_WINDOW+1e-9):
                # A knee lift is a one-shot edge. Keep it through this punch's
                # recovery without cancelling the punch or retaining a combo
                # backlog. Pause/lost tracking still clears the shared slot.
                max_age=min(self.KICK_COMBO_MAX_AGE,remaining+.04)
            elif (attack and opposite is not None and attack['move']==opposite
                    and remaining <= MOVES[opposite]['duration']+1e-9):
                # The palm decision can resolve alternating punches together.
                max_age=min(self.ALTERNATING_MAX_AGE,remaining+.04)
            else:
                return 'busy'
        self.pending = dict(game=game, move=move, created=now,
                            game_time=game.time, max_age=max_age)
        return 'queued'

    def flush(self, game, now):
        """Return accepted once, queued while waiting, or a discard status.

        idle means there was no pending input. expired/inactive/no_energy
        discard the input without attacking. Neither method cancels an attack
        or overrides Fight's cooldown and energy checks.
        """
        if not self._valid_time(now):
            self.clear()
            return 'invalid'
        if game.state != 'playing':
            self.clear()
            return 'inactive'
        if self._expire(game, now):
            return 'expired'
        if self.pending is None:
            return 'idle'
        move = self.pending['move']
        if not self._has_energy(game, move):
            self.clear()
            return 'no_energy'
        if game.punch(move):
            self.clear()
            return 'accepted'
        return 'queued'
