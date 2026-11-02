"""Runner host/visitor contracts; local fixtures only, no camera or board I/O."""
import ast
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
# A sparse review overlay may use unchanged package helpers from its full
# production copy. Normal deployment has every helper in ROOT already.
SUPPORT=ROOT.parent/'working'/'ink-suite'
if SUPPORT.is_dir():
    sys.path.append(str(SUPPORT))
    for name in ('visitor','audio'):
        package=types.ModuleType(name)
        package.__path__=[str(ROOT/name),str(SUPPORT/name)]
        package.__package__=name
        sys.modules[name]=package

import action_catalog
import action_dispatch
import radial_layout
import resource_manifest
from visitor.store import VisitorStore,summary
from visitor.reporter import GameReporter

TMP=Path(os.environ.get('INK_RUNNER_TEST_ROOT',str(ROOT/'diagnostics'/'runner-integration')))
TMP.mkdir(parents=True,exist_ok=True)


def literals(path,name):
    tree=ast.parse((ROOT/path).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(n,ast.Name) and n.id==name for n in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


class HostContracts(unittest.TestCase):
    def test_existing_order_and_seventh_geometry(self):
        from gesture_menu import DwellSelector
        self.assertEqual(radial_layout.MODES,('ink','sword','fight','fruit','taiko','platformer','runner','pal','kart'))
        self.assertEqual(len(radial_layout.geometry()[1]),9)
        for index,(x,y,radius) in enumerate(radial_layout.geometry()[1]):
            self.assertEqual(radial_layout.hit_index(x/960,y/540),index)
        from experience_names import TITLES
        self.assertEqual(TITLES,action_catalog.TITLES)
        self.assertEqual(TITLES['runner'],'地铁跑酷')
        self.assertEqual(TITLES['pal'],'仙剑奇侠传')
        selector=DwellSelector()
        selector.set_choices(radial_layout.MODES,radial_layout.rectangles(),hit_test=radial_layout.hit_index)
        self.assertEqual(len(selector.choices),9)

    def test_backend_mapping_and_child_launcher(self):
        tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        host=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(isinstance(m,ast.FunctionDef) and m.name=='backend_mode' for m in n.body))
        method=next(n for n in host.body if isinstance(n,ast.FunctionDef) and n.name=='backend_mode')
        method.decorator_list=[]
        namespace={};exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),'backend_mode','exec'),namespace)
        self.assertEqual(namespace['backend_mode']('runner'),'sword')
        self.assertEqual(namespace['backend_mode']('platformer'),'sword')
        self.assertEqual(namespace['backend_mode']('fruit'),'fruit')
        source=(ROOT/'main.py').read_text(encoding='utf-8')
        self.assertIn("if mode in ('platformer','runner','pal','kart'):",source)
        self.assertIn("str(ROOT/mode/'main.py')",source)
        self.assertIn("if mode=='fruit':\n                    try:\n                        from fruit_pixman_runtime import build_child_environment",source)

    def test_restart_and_audio_actions_are_available(self):
        available=action_catalog.actions('runner')
        self.assertTrue({'restart','resume','home','help','audio_retry','audio_low'}<=available)
        self.assertEqual(action_dispatch.SUPPORTED_ACTIONS['runner'],action_dispatch.GAME|action_dispatch.AUDIO)
        calls=[];results=[]
        def execute(action,done):calls.append(action);done(True,game_state='paused')
        dispatcher=action_dispatch.ActionDispatcher('runner',lambda:True,execute,lambda event,**packet:results.append((event,packet)))
        request=dict(op='action',command_id='one',action='restart')
        dispatcher.dispatch(request);dispatcher.dispatch(request)
        self.assertEqual(calls,['restart'])
        self.assertTrue(all(event=='action_result' and packet['ok'] for event,packet in results))

    def test_resource_whitelist_and_cube_mode(self):
        expected=('manifest.json','bgm_runner.wav')+tuple('sfx_runner_'+n+'.wav' for n in ('jump','slide','lane','coin','power','hit','over'))
        for name in expected:self.assertTrue(resource_manifest.resource_allowed('runner/audio_assets/'+name),name)
        self.assertFalse(resource_manifest.resource_allowed('runner/audio_assets/arbitrary.wav'))
        for name in resource_manifest.PRIVATE_PIXMAN_RESOURCES:self.assertTrue(resource_manifest.resource_allowed(name),name)
        self.assertEqual(literals('audio/runtime.py','MUSIC_IDS')['runner'],'bgm_runner')
        tree=ast.parse((ROOT/'audio/game_output.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CubeGameAudioDirector')
        node=next(n for n in cls.body if isinstance(n,ast.Assign) and any(isinstance(k,ast.Name) and k.id=='MODES' for k in n.targets))
        self.assertIn('runner',ast.literal_eval(node.value.args[0]))


class VisitorContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=TMP)
        self.store=VisitorStore(self.temp.name)
        self.session=self.store.current()

    def tearDown(self):self.temp.cleanup()

    def test_runner_round_and_leaderboard_separation(self):
        rid=uuid.uuid4().hex
        self.assertTrue(self.store.record(self.session['id'],rid,'runner',dict(score=321,coins=4,elapsed=12,distance=42.5,counts={'jump':2}),'completed'))
        self.assertTrue(self.store.record(self.session['id'],uuid.uuid4().hex,'fruit',dict(score=900),'completed'))
        view=self.store.view(self.session['token'])
        row=next(r for r in view['rounds'] if r['game']=='runner')
        self.assertEqual(row['title'],'地铁跑酷')
        self.assertEqual(row['data']['distance'],42.5)
        board=self.store.leaderboard('runner')
        self.assertEqual(board[0]['game'],'runner')
        self.assertEqual(board[0]['items'][0]['score'],321)
        self.assertEqual(len(board),1)

    def test_running_state_recorded_and_restart_is_new_round(self):
        class Game:
            state='running';elapsed=1.
            def report(self):return dict(score=50,coins=1,distance=3.,elapsed=self.elapsed,counts={'jump':1})
        game=Game()
        reporter=GameReporter(self.store,self.session['id'],'runner')
        try:
            reporter.observe(game)
            self.assertIsNotNone(reporter.round_id)
            game.state='over';game.elapsed=5.;reporter.observe(game)
            reporter.jobs.join()
            game.state='running';game.elapsed=0.;reporter.observe(game)
            game.state='over';game.elapsed=3.;reporter.observe(game)
            reporter.jobs.join()
        finally:reporter.close()
        rounds=self.store.view(self.session['token'])['rounds']
        self.assertEqual(len(rounds),2)
        self.assertTrue(all(r['status']=='completed' for r in rounds))
        self.assertEqual(len({r['id'] for r in rounds}),2)

    def test_report_png_keeps_distance_and_round(self):
        from visitor.report_image import _core_stats,render_report_page,GAME_ORDER
        from PIL import Image
        self.assertIn('runner',GAME_ORDER)
        self.store.record(self.session['id'],uuid.uuid4().hex,'runner',dict(score=1234,coins=21,elapsed=19.5,distance=138.8,counts={'jump':5,'slide':3}),'completed')
        report=self.store.view(self.session['token'])
        stats=dict(_core_stats(report['rounds'][0]))
        self.assertEqual(stats['金币'],'21')
        self.assertEqual(stats['跳跃'],'5')
        png=render_report_page(report)
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.width,1080)
            self.assertLessEqual(image.height,7200)
            metadata=json.loads(image.info['InkSuiteReport'])
            self.assertEqual(metadata['round_ids'],[report['rounds'][0]['id']])
        # A real preview for human review, isolated from artwork and live data.
        (TMP/'runner-score-report.png').write_bytes(png)


if __name__=='__main__':unittest.main()
