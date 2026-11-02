"""Six-entry suite and platformer visitor-report regressions, no camera/network."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from PIL import Image
from action_catalog import TITLES as MENU_TITLES, HELP, actions, page
from action_dispatch import ActionDispatcher, SUPPORTED_ACTIONS
from gesture_menu import DwellSelector
from radial_layout import MODES, VISITOR_RECTS, geometry, hit_index, rectangles
from platformer.engine import Game
from visitor import report_image
from visitor.reporter import GameReporter, attach
from visitor.store import VisitorStore, GAMES, summary


def report_rows(count=1):
    rows=[]
    for i in range(count):
        rows.append(dict(id=f'{i+1:032x}',game='platformer',title='超级玛丽国风版',
                         status='completed',rules='standard',score=700+i,
                         rules_label='标准玩法',started='2026-10-04T12:00:00+00:00',
                         ended='2026-10-04T12:01:00+00:00',
                         data=dict(score=700+i,coins=7,completed_stages=3,
                                   enemies_defeated=4,lives=2)))
    return dict(name='云关测试游客',status='active',started='2026-10-04T12:00:00+00:00',
                ended=None,rounds=rows,artworks=[])


class SixEntryTests(unittest.TestCase):
    def test_dynamic_six_entry_hit_targets_and_visitor_offsets(self):
        self.assertEqual(MODES,('ink','sword','fight','fruit','taiko','platformer'))
        self.assertEqual(DwellSelector().choices,MODES)
        for w,h in ((640,360),(960,540),(1024,600),(1920,1080)):
            center,circles=geometry(w,h)
            self.assertEqual(len(circles),6)
            for i,(x,y,radius) in enumerate(circles):
                self.assertEqual(hit_index(x/w,y/h,w,h),i)
            self.assertEqual(hit_index(center[0]/w,center[1]/h,w,h),len(MODES))
            self.assertEqual(len(rectangles(w,h,visitors=True)),len(MODES)+3)
            for i,(a,b,c,d) in enumerate(VISITOR_RECTS,len(MODES)+1):
                self.assertEqual(hit_index((a+c)/2,(b+d)/2,w,h,visitors=True),i)

    def test_platformer_catalog_and_dispatcher_agree_on_child_actions(self):
        self.assertEqual(MENU_TITLES['platformer'],'超级玛丽国风版')
        self.assertTrue(HELP['platformer'])
        self.assertEqual(page('platformer','help')['help'],HELP['platformer'])
        help_text=''.join(HELP['platformer'])
        for cue in ('摇杆中点','回中或放下即停','肩线','向下压','站稳停住'):
            self.assertIn(cue,help_text)
        self.assertNotIn('快速扬起',help_text)
        self.assertNotIn('双手高举',help_text)
        root_ids={item['id'] for item in page('platformer')['items']}
        self.assertTrue({'resume','restart','help','home'}<=root_ids)
        # audio_options is a host navigation page, while its leaf items are
        # child IPC actions. Do not send page navigation to the dispatcher.
        child_actions={'restart'}|{
            item['id'] for name in ('audio','audio_options')
            for item in page('platformer',name)['items']
            if item['id'] not in ('back','audio_options')}
        self.assertTrue(child_actions<=SUPPORTED_ACTIONS['platformer'])
        execute,send=Mock(),Mock()
        def finish(action,done):done(True,game_state='paused')
        execute.side_effect=finish
        dispatcher=ActionDispatcher('platformer',lambda:True,execute,send)
        request=dict(op='action',command_id='platformer.restart.1',action='restart')
        dispatcher.dispatch(request);dispatcher.dispatch(request)
        execute.assert_called_once()
        self.assertEqual(send.call_count,2)
        self.assertEqual(send.call_args_list[0],send.call_args_list[1])


class ReporterStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=VisitorStore(self.temp.name)
        self.session=self.store.current()
        # No async writer or hardware worker is launched in these tests.
        with patch('visitor.reporter.threading.Thread'):
            self.reporter=GameReporter(self.store,self.session['id'],'platformer')
        self.game=Game()
        self.game.start(0)
        self.game.elapsed=12.25
        self.game.score=1700
        self.game.coins=17
        self.game.stage_clears=2
        self.game.stomps=6
        self.game.level_index=2
        self.game.deaths=1
        self.game.pipes_used=3

    def jobs(self):
        rows=[]
        while not self.reporter.jobs.empty():
            value=self.reporter.jobs.get_nowait()
            if value is not None:rows.append(value)
        return rows

    def test_report_aliases_reach_real_sqlite_without_building_scene_snapshot(self):
        with patch.object(self.game,'snapshot',side_effect=AssertionError('Scene built by reporter')):
            self.reporter.observe(self.game)
        jobs=self.jobs()
        self.assertEqual(len(jobs),1)
        data=jobs[0][3]
        self.assertEqual(data['coins'],17)
        self.assertEqual(data['completed_stages'],2)
        self.assertEqual(data['enemies_defeated'],6)
        self.assertEqual(data['level_index'],2)
        self.assertEqual(data['deaths'],1)
        self.assertEqual(data['pipes_used'],3)
        self.assertEqual(data['duration'],12.25)
        self.assertNotIn('points17',data)
        self.assertNotIn('player',data)
        self.assertTrue(self.store.record(*jobs[0]))
        row=self.store.view(self.session['token'])['rounds'][0]
        self.assertEqual(row['title'],'超级玛丽国风版')
        self.assertEqual(row['data'],data)
        self.assertEqual(row['score'],1700)

    def test_terminal_completion_is_durable_once_and_old_progress_cannot_overwrite(self):
        self.reporter.observe(self.game)
        progress=self.jobs()[0]
        self.store.record(*progress)
        self.game.state='won'
        self.game.stage_clears=3
        self.reporter.observe(self.game)
        self.reporter.observe(self.game)
        jobs=self.jobs()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0][4],'completed')
        self.assertEqual(jobs[0][3]['result'],'won')
        self.assertEqual(jobs[0][3]['completed_stages'],3)
        pending=list(Path(self.temp.name).glob('pending-*.json'))
        self.assertEqual(len(pending),1)
        self.assertEqual(json.loads(pending[0].read_text(encoding='utf-8'))[3]['result'],'won')
        self.assertTrue(self.store.record(*jobs[0]))
        self.assertFalse(self.store.record(*progress))
        saved=self.store.view(self.session['token'])['rounds'][0]
        self.assertEqual(saved['status'],'completed')
        self.assertEqual(saved['data']['result'],'won')

    def test_restart_wrapper_captures_terminal_before_engine_resets_counters(self):
        self.reporter.observe(self.game)
        self.jobs()
        self.game.state='over'
        panel=SimpleNamespace(game=self.game,operation=Mock(),
                              restart=lambda:self.game.restart(0))
        glib=SimpleNamespace(timeout_add=Mock(return_value=1))
        with patch.object(GameReporter,'from_environment',return_value=self.reporter):
            attach(panel,'platformer',glib)
        panel.restart()
        jobs=self.jobs()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0][4],'completed')
        self.assertEqual(jobs[0][3]['score'],1700)
        self.assertEqual(jobs[0][3]['result'],'over')
        self.assertEqual(self.game.score,0)
        self.assertIsNone(self.reporter.round_id)
        self.reporter.observe(self.game)
        self.assertEqual(self.jobs()[0][4],'active')

    def test_playing_interruption_and_leaderboards_stay_separate(self):
        self.reporter.observe(self.game)
        self.jobs()
        self.reporter.close(self.game)
        jobs=self.jobs()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0][4],'interrupted')
        self.store.record(*jobs[0])
        self.assertEqual(self.store.leaderboard('platformer'),[])
        self.store.record(self.session['id'],'f'*32,'platformer',dict(score=800),'completed')
        self.store.record(self.session['id'],'e'*32,'sword',dict(score=90000),'completed')
        board=self.store.leaderboard('platformer')
        self.assertEqual(len(board),1)
        self.assertEqual(board[0]['game'],'platformer')
        self.assertEqual(board[0]['items'][0]['score'],800)

    def test_json_csv_html_exports_preserve_platformer_aliases(self):
        self.store.record(self.session['id'],'d'*32,'platformer',self.game.report(),'completed')
        payload=json.loads(self.store.report(self.session['token'],'json'))
        data=payload['rounds'][0]['data']
        self.assertEqual(data['completed_stages'],2)
        self.assertEqual(data['enemies_defeated'],6)
        for kind in ('html','csv'):
            value=self.store.report(self.session['token'],kind).decode('utf-8-sig')
            self.assertIn('超级玛丽国风版',value)
            self.assertIn('completed_stages',value)

    def test_summary_filters_pose_images_and_unrecognized_report_fields(self):
        raw=dict(self.game.report(),points17=[[.5,.5,.9]]*17,
                 image=b'not an image',player={'x':3},latency_ms=90)
        bounded=summary(raw)
        self.assertEqual(bounded['completed_stages'],2)
        self.assertTrue({'points17','image','player','latency_ms'}.isdisjoint(bounded))


class ReportImageTests(unittest.TestCase):
    def test_fifth_game_overview_does_not_overlap_round_heading(self):
        report=report_rows()
        with patch.object(report_image,'_text',wraps=report_image._text) as draw_text:
            png=report_image.render_report_page(report)
        locations={}
        for call in draw_text.call_args_list:
            _,position,value,*_=call.args
            locations.setdefault(value,[]).append(position)
        # The first 超级玛丽国风版 occurrence is its overview card; reserve the entire
        # card's height before the separately rendered per-round section.
        overview_title_y=locations['超级玛丽国风版'][0][1]
        round_heading_y=locations['逐局战绩'][0][1]
        self.assertGreaterEqual(round_heading_y,overview_title_y+110)
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.width,report_image.WIDTH)
            self.assertLessEqual(image.height,report_image.MAX_HEIGHT)
            self.assertEqual(json.loads(image.info['InkSuiteReport'])['round_ids'],['0'*31+'1'])

    def test_first_page_reserves_third_overview_row_and_full_artwork(self):
        report=report_rows(20)
        ident='b'*32
        report['artworks']=[dict(id=ident,title='合成水墨',marks=1)]
        image=Image.new('RGB',(1080,380),'#e3d8bf')
        buffer=io.BytesIO();image.save(buffer,format='PNG');image.close()
        reader=Mock(return_value=buffer.getvalue())
        manifest=report_image.report_manifest(report)
        self.assertEqual(manifest['pages'][0]['last_round'],19)
        self.assertEqual(manifest['page_count'],2)
        png=report_image.render_report_page(report,1,reader)
        reader.assert_called_once_with(ident)
        with Image.open(io.BytesIO(png)) as image:
            self.assertLessEqual(image.height,report_image.MAX_HEIGHT)
            self.assertEqual(len(json.loads(image.info['InkSuiteReport'])['round_ids']),19)
        ids=[r for item in manifest['pages'] for r in item['round_ids']]
        self.assertEqual(ids,[r['id'] for r in report['rounds']])

    def test_platformer_counters_and_outcomes_have_specific_labels(self):
        row=report_rows()['rounds'][0]
        fields=dict(report_image._core_stats(row))
        self.assertEqual(fields,{'铜钱':'7','通关数':'3','击败敌人':'4','剩余生命':'2'})
        row['data'].update(result='won',deaths=1,pipes_used=2)
        detail=report_image._detail(row)
        self.assertIn('三关通关',detail)
        self.assertIn('失误 1 次',detail)
        self.assertIn('云管 2 次',detail)

    def test_platformer_icon_is_a_traveller_not_legacy_fish_fallback(self):
        draw=Mock()
        report_image._icon(draw,'platformer',100,100)
        self.assertGreaterEqual(draw.rectangle.call_count,8)
        draw.ellipse.assert_not_called()

    def test_report_names_match_store_and_catalog_and_style_invalidates_old_cache(self):
        self.assertEqual(report_image.GAMES['platformer'],GAMES['platformer'])
        self.assertEqual(report_image.GAMES['platformer'],MENU_TITLES['platformer'])
        self.assertIn('platformer',report_image.GAME_ORDER)
        self.assertNotEqual(report_image.IMAGE_STYLE_VERSION,'ink-report-v1-20261003')


if __name__=='__main__':
    unittest.main()
