"""Opt-in round evidence. Camera images are written by the existing pose process."""
import json
import os
import threading
import time
from pathlib import Path

CONTROL = Path('/run/user/1000/ink-fight-evidence.json')


class SessionRecord:
    def __init__(self, directory, control=CONTROL):
        self.directory = Path(directory).resolve()
        self.control = Path(control)
        self.started = False
        self.closed = False
        self.rows = []
        self.started_at = 0.
        self.writer = None

    def start(self):
        if self.started:
            return
        self.directory.mkdir(parents=True, exist_ok=False)
        self.started_at = time.monotonic()
        payload = dict(directory=str(self.directory), expires_monotonic=self.started_at + 125., owner_pid=os.getpid())
        temporary = self.control.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload), encoding='utf-8')
        temporary.replace(self.control)
        self.started = True

    def observe(self, pose, game, moves, feedback, valid, both_valid):
        if not self.started or self.closed or len(self.rows) >= 12000:
            return
        self.rows.append(dict(
            monotonic=time.monotonic(),
            pose={k: pose.get(k) for k in ('pid', 'sequence', 'timestamp_seconds', 'points17', 'pose_fps', 'latency_ms', 'stale')} if isinstance(pose, dict) else None,
            game_time=game.time, state=game.state, hp=game.hp, enemy_hp=game.enemy_hp,
            energy=game.energy, recognized=list(moves),
            active_move=game.attack['move'] if game.attack else None,
            move_counts=dict(game.move_counts), feedback=feedback, valid=valid,
            both_valid=both_valid, guard=game.guard))

    def finish(self, reason, events=()):
        if not self.started or self.closed:
            return
        self.closed = True
        try:
            payload = json.loads(self.control.read_text(encoding='utf-8'))
            if payload.get('directory') == str(self.directory):
                self.control.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        self.writer = threading.Thread(target=self._save, args=(reason, list(events)), daemon=False)
        self.writer.start()

    def _save(self, reason, events):
        summary = dict(reason=reason, started_monotonic=self.started_at,
                       finished_monotonic=time.monotonic(), state_count=len(self.rows),
                       timestamps='Board CLOCK_MONOTONIC. JPEGs and their pose points use the same producer frame. Game consumes latest pose independently; match source timestamps.',
                       recording='Existing pose preview queue, bounded asynchronous 8 FPS evidence; see camera-frames.jsonl for actual cadence.')
        with (self.directory / 'states.jsonl').open('w', encoding='utf-8') as f:
            for row in self.rows:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        (self.directory / 'input-events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        (self.directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
