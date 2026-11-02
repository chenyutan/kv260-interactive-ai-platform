"""Single master-clock session and transport lifecycle, testable without GTK."""
import math
from collections import Counter, deque
from .engine import Game, INPUT_GRACE, judgment_rules
from .chart import chart, practice_window, DURATION


class Session:
    def __init__(self, clock, difficulty='easy', offset_ms=0., practice='full', judgment='strict'):
        self.clock = clock
        self.difficulty = difficulty
        self.practice = practice
        judgment_rules(judgment)
        self.judgment = judgment
        self.running = False
        self.error = None
        self.resume_epoch = None
        self._position_ready = False
        self._restart_pending = False
        self.input_counts = Counter()
        self.input_reasons = Counter()
        self.input_diagnostics = deque(maxlen=64)
        self.set_offset_ms(offset_ms)
        self.game = self._new_game()
        # A nonzero practice needs a successful seek before any score is allowed.
        self._position_ready = self.game.start_time == 0.

    def _new_game(self, judgment=None):
        profile = self.judgment if judgment is None else judgment
        outer = judgment_rules(profile)['outer']
        start, end, lead_in = practice_window(self.practice)
        notes = [n for n in chart(self.difficulty)
                 if max(start, lead_in) <= n['time'] < end - outer]
        grace = INPUT_GRACE + max(0., self.offset)
        game = Game(self.difficulty, notes=notes, duration=min(DURATION, end + grace), start_time=start,
                    practice=self.practice, lead_in_until=lead_in, input_grace=grace, judgment=profile)
        game.practice_end = end
        return game

    def set_judgment(self, profile):
        """Change tolerance only before any input or settled note in a paused round.

        Restart explicitly before changing a used round. No old judgment is
        re-evaluated, and the native music clock is never sought by this method.
        """
        judgment_rules(profile)
        if self.running or not self.game.paused:
            raise RuntimeError('Pause before changing judgment profile')
        if self.game.strikes or any(self.game.counts.values()):
            raise RuntimeError('Restart before changing judgment profile')
        if profile == self.judgment:
            return True
        candidate = self._new_game(profile)
        candidate.time = self.game.time
        self.game = candidate
        self.judgment = profile
        return True

    def set_offset_ms(self, value):
        if not math.isfinite(value) or abs(value) > 500:
            raise ValueError('Calibration must be between -500 and 500 ms')
        self.offset = value / 1000
        if hasattr(self, 'game'):
            self.game.input_grace = INPUT_GRACE + max(0., self.offset)
            self.game.duration = max(self.game.time, min(DURATION,
                self.game.practice_end + self.game.input_grace))

    def pause(self):
        # Preparing/lost UI states may call at rendering frequency; do not
        # repeatedly change the native GStreamer pipeline state.
        if self.running:
            self.clock.pause()
        self.game.pause(True)
        self.running = False
        self.resume_epoch = None

    def resume(self):
        if self.running:
            return True
        if self._restart_pending and not self.restart():
            return False
        if self.game.finished:
            return False
        if not self._position_ready:
            if not self.clock.seek(self.game.time):
                self.error = self.clock.error or '音频定位未完成'
                return False
            self._position_ready = True
        if not self.clock.play():
            # A backend can partially transition before reporting failure.
            self.clock.pause()
            self.error = self.clock.error or '音频播放未启动'
            return False
        self.error = None
        self.game.pause(False)
        self.running = True
        self.resume_epoch = self.clock.now()
        return True

    def restart(self):
        self.pause()
        candidate = self._new_game()
        self._position_ready = False
        self._restart_pending = True
        if not self.clock.seek(candidate.start_time):
            self.error = self.clock.error or '音频定位未完成'
            return False
        self.game = candidate
        self._position_ready = True
        self._restart_pending = False
        self.error = None
        return True

    def _record_input(self, event, now, reason, audio_at=None, judgment=None):
        stamp=event.get('timestamp')
        valid=lambda value: isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
        age=now-stamp if valid(now) and valid(stamp) else None
        judge_at=audio_at-age-self.offset if audio_at is not None and age is not None else None
        row=dict(disposition=reason, reason=reason, received_monotonic=now, event_timestamp=stamp if valid(stamp) else None,
                 source_stamp=event.get('source_stamp'), source_sequence=event.get('source_sequence'),
                 previous_source_stamp=event.get('previous_source_stamp'),
                 crossing_line=event.get('crossing_line'),old_normalized_y=event.get('old_normalized_y'),
                 new_normalized_y=event.get('new_normalized_y'),
                 path=event.get('path'), hand=event.get('hand'), event_id=str(event.get('id'))[:160],
                 event_age=age, audio_position=audio_at, judge_at=judge_at, offset_ms=self.offset*1000,
                 resume_epoch=self.resume_epoch, judgment=self.game.judgment,
                 hit_window_ms=self.game.judgment_rules['outer']*1000,
                 simultaneous_ms=self.game.judgment_rules['simultaneous']*1000)
        if judgment:
            row.update(judgment)
        elif judge_at is not None and event.get('hand') in ('L','R','C'):
            # Context for the rejected candidate, never a retrospective hit.
            row.update(self.game.nearest_note(event['hand'],judge_at))
        self.input_counts[reason]+=1
        self.input_reasons[row['reason']]+=1
        self.input_diagnostics.append(row)

    def input_snapshot(self):
        return dict(counts=dict(self.input_counts), reasons=dict(self.input_reasons),
                    recent=[dict(row) for row in self.input_diagnostics])

    def tick(self, now, events=()):
        if not self.running:
            for event in events:self._record_input(event,now,'paused')
            return
        if not self.clock.poll():
            self.error = self.clock.error
            for event in events:self._record_input(event,now,'audio_error')
            self.pause()
            return
        at = self.clock.position()
        if self.clock.error:
            self.error = self.clock.error
            for event in events:self._record_input(event,now,'audio_error',at)
            self.pause()
            return
        for event in events:
            stamp=event.get('timestamp')
            if (not isinstance(stamp,(int,float)) or isinstance(stamp,bool) or not math.isfinite(stamp)
                    or event.get('hand') not in ('L','R','C') or 'id' not in event):
                self._record_input(event,now,'invalid_event',at);continue
            age=now-stamp
            if not -.05-1e-9 <= age <= .45+1e-9:
                self._record_input(event,now,'expired' if age>.45 else 'future',at);continue
            if self.resume_epoch is None or stamp<self.resume_epoch:
                self._record_input(event,now,'before_epoch',at);continue
            if (event['hand'],str(event['id'])) in self.game.seen:
                self._record_input(event,now,'duplicate',at);continue
            judge_at=at-age-self.offset
            before=self.game.strikes
            self.game.strike(event['hand'],judge_at,event['id'],display_time=at)
            if self.game.strikes==before:
                self._record_input(event,now,'outside_game',at);continue
            judgment=dict(self.game.last_strike_diagnostic)
            self._record_input(event,now,judgment['outcome'],at,judgment)
        if self.clock.eos:
            at = self.game.duration
        self.game.advance(at)
        if self.game.finished:
            self.pause()

    def replace_clock(self, clock):
        self.pause()
        position = self.game.time
        self.clock.close()
        self.clock = clock
        self._position_ready = bool(clock.seek(position))
        self.error = None if self._position_ready else clock.error or '音频定位未完成'
        return self._position_ready

    def close(self):
        self.pause()
        self.clock.close()
