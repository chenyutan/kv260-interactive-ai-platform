import json
from pathlib import Path
import tempfile
import unittest
from engine import Fight
from session_record import SessionRecord


class SessionRecordTests(unittest.TestCase):
    def test_idle_does_not_create_control_or_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rec=SessionRecord(root/'round',root/'control.json')
            rec.observe(None,Fight(),[], '',False,False)
            rec.finish('closed')
            self.assertFalse(rec.control.exists())
            self.assertFalse(rec.directory.exists())

    def test_round_opt_in_and_final_data(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rec=SessionRecord(root/'round',root/'control.json')
            rec.start();payload=json.loads(rec.control.read_text())
            self.assertEqual(payload['directory'],str(rec.directory))
            self.assertGreater(payload['expires_monotonic'],rec.started_at)
            game=Fight();game.start();game.advance(3.)
            rec.observe({'sequence':7,'timestamp_seconds':123.,'points17':[]},game,['left'],'busy',True,True)
            rec.finish('round_over',[{'result':'busy'}]);rec.writer.join(2)
            self.assertFalse(rec.control.exists())
            row=json.loads((rec.directory/'states.jsonl').read_text())
            self.assertEqual(row['recognized'],['left'])
            self.assertEqual(row['pose']['sequence'],7)
            self.assertEqual(json.loads((rec.directory/'summary.json').read_text())['reason'],'round_over')
            self.assertEqual(json.loads((rec.directory/'input-events.json').read_text()),[{'result':'busy'}])

    def test_finish_does_not_remove_other_session_control(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rec=SessionRecord(root/'round',root/'control.json');rec.start()
            rec.control.write_text(json.dumps({'directory':'another-session'}))
            rec.finish('closed');rec.writer.join(2)
            self.assertTrue(rec.control.exists())


if __name__=='__main__':unittest.main()
