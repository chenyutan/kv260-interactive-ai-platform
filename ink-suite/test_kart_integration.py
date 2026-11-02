"""Ninth-entry and persistent report acceptance without hardware or live input."""
import math,tempfile,unittest,uuid
from pathlib import Path
from radial_layout import MODES,geometry,hit_index
from entry_help import briefing
from action_catalog import actions
from action_dispatch import GAME,AUDIO,SUPPORTED_ACTIONS
from visitor.store import VisitorStore
from visitor.reporter import GameReporter
from kart.engine import KartGame

class KartIntegration(unittest.TestCase):
    def test_interrupted_report_does_not_claim_current_lap_completed(self):
        from visitor.report_image import _detail
        row=dict(game='kart',status='interrupted',data=dict(result='running',lap=1))
        self.assertIn('当前第 1 圈',_detail(row))
        self.assertNotIn('完成 1 圈',_detail(row))
    def test_ninth_entry_and_hit_targets_at_supported_sizes(self):
        self.assertEqual(MODES[-1],'kart');self.assertEqual(len(MODES),9)
        for w,h in ((960,540),(1280,720),(640,360),(640,420)):
            _,circles=geometry(w,h)
            for i,(x,y,r) in enumerate(circles):
                self.assertEqual(hit_index(x/w,y/h,w,h),i)
                for xx,yy,rr in circles[i+1:]:self.assertGreater(math.hypot(x-xx,y-yy),r+rr)
    def test_both_control_choices_and_secondary_actions(self):
        for mode in ('body','gamepad'):
            help=briefing('kart',mode)
            self.assertEqual(help['title'],'赛车 · 开始前');self.assertEqual(len(help['lines']),4)
        self.assertTrue({'restart','home','help','audio_mute','audio_retry'}<=actions('kart'))
        self.assertEqual(SUPPORTED_ACTIONS['kart'],GAME|AUDIO)
    def test_record_download_and_board_keep_existing_games(self):
        with tempfile.TemporaryDirectory() as temp:
            store=VisitorStore(temp);session=store.current()
            store.record(session['id'],uuid.uuid4().hex,'runner',dict(score=123,distance=50),'completed')
            game=KartGame();reporter=GameReporter(store,session['id'],'kart')
            game.start();reporter.observe(game,running=True)
            game.state='finished';game.elapsed=91.;game.rank=2
            reporter.observe(game,running=False);reporter.close()
            view=store.view(session['token'])
            kart=next(row for row in view['rounds'] if row['game']=='kart')
            self.assertEqual(kart['status'],'completed');self.assertEqual(kart['title'],'赛车')
            self.assertEqual(kart['data']['result'],'finished')
            self.assertEqual(store.leaderboard('runner')[0]['items'][0]['score'],123)
            self.assertTrue(store.leaderboard('kart')[0]['items'])
    def test_finish_then_host_pause_before_reporter_observe_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            store=VisitorStore(temp);session=store.current()
            store.record(session['id'],uuid.uuid4().hex,'runner',dict(score=123,distance=50),'completed')
            game=KartGame();reporter=GameReporter(store,session['id'],'kart')
            try:
                game.start();reporter.observe(game)
                game.state='running';game.elapsed=12.;reporter.observe(game)
                # Host pause arrives before the next 200ms terminal observer.
                game.state='finished';game.elapsed=74.383;game.score=252927
                game.rank=4;game.lap=3;game.player.finish_time=game.elapsed
                game.pause();self.assertEqual(game.state,'finished')
                reporter.observe(game);reporter.observe(game)
                # Prepare restart while the host still owns the paused child.
                game.restart();game.pause();reporter.reset();game.resume();reporter.observe(game)
            finally:reporter.close(game)
            rows=[r for r in store.view(session['token'])['rounds'] if r['game']=='kart']
            self.assertEqual(len(rows),2)
            completed=[r for r in rows if r['status']=='completed']
            self.assertEqual(len(completed),1)
            self.assertEqual(completed[0]['data']['result'],'finished')
            self.assertEqual(completed[0]['score'],252927)
            self.assertEqual(completed[0]['data']['position'],4)
            self.assertEqual(sorted(r['status'] for r in rows),['completed','interrupted'])
            self.assertEqual(store.leaderboard('kart')[0]['items'][0]['score'],252927)
            self.assertEqual(store.leaderboard('runner')[0]['items'][0]['score'],123)

if __name__=='__main__':unittest.main()
