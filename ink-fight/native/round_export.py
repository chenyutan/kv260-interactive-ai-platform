"""Atomic summaries of completed InkFight rounds; no camera or pose data."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class RoundExport:
    def __init__(self, directory, enabled=True):
        self.directory = Path(directory)
        self.enabled = enabled
        self.round_id = None
        self.started_at = None
        self.saved = False

    def start(self):
        self.round_id = uuid.uuid4().hex
        self.started_at = utc_now()
        self.saved = False

    def finish(self, game):
        """Called only at round completion. Retry is safe after a disk error."""
        if not self.enabled or self.saved or not self.round_id or game.state != 'over':
            return None
        summary = dict(schema=1, game='ink-fight', version='2.5',
                       round_id=self.round_id, completed=True,
                       started_at=self.started_at, completed_at=utc_now(),
                       elapsed=game.time, result=game.result, hp=game.hp,
                       enemy_hp=game.enemy_hp, hits=game.hits, blocks=game.blocks,
                       perfect_blocks=game.perfect_blocks,
                       move_counts=dict(game.move_counts))
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (self.round_id + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        self.saved = True
        return summary
