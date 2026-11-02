"""Exercise actual worker bodies with fake pixels/events, without loading DPU/camera."""
import ast,json,logging,queue,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image,ImageDraw
from suite_control import SuiteControl

class Preprocessor:
    def resize(self,raw,w,h,stride,size):
        return np.full((size[1],size[0],3),70,dtype=np.uint8)

tree=ast.parse((Path(__file__).parent/'runtime/app.py').read_text(encoding='utf-8'))
live=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LiveApp')
names={'preview_worker','paint_worker'}
methods=[n for n in live.body if isinstance(n,ast.FunctionDef) and n.name in names]
scope=dict(np=np,Image=Image,ImageDraw=ImageDraw,queue=queue,time=time,json=json,logging=logging,
           YuyvPreprocessor=Preprocessor,EDGES=[],draw_pose=lambda d,*_:d.point((1,1),fill=(255,0,0)))
exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),'actual_runtime_workers','exec'),scope)

class RuntimeWorkerTests(unittest.TestCase):
    def make(self):
        app=SimpleNamespace(suite=SuiteControl(),stop=threading.Event(),lock=threading.RLock(),
            args=SimpleNamespace(preview_width=384),preview_queue=queue.Queue(1),paint_queue=queue.Queue(4),
            environment_queue=queue.Queue(1),preview_version=0,environment_version=0,environment_frame=None,raw_preview=True,
            overlay_thresholds=np.ones(17)*.1,generation=0,paint_lock=threading.Lock(),state={})
        app.preview_changed=threading.Condition(app.lock)
        for name in names:setattr(app,name,scope[name].__get__(app))
        return app
    def spawn(self,app,name):
        errors=[]
        def work():
            try:getattr(app,name)()
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=work);thread.start()
        def clean():
            app.stop.set();thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.addCleanup(clean)
        return thread
    def wait(self,predicate):
        end=time.monotonic()+2
        while not predicate() and time.monotonic()<end:time.sleep(.005)
        self.assertTrue(predicate())
    def test_no_continuous_environment_capture_or_conversion_worker(self):
        self.assertNotIn('environment_worker',[n.name for n in live.body if isinstance(n,ast.FunctionDef)])
        capture=next(n for n in live.body if isinstance(n,ast.FunctionDef) and n.name=='capture')
        self.assertNotIn('environment',ast.unparse(capture))
    def test_standalone_overlay_is_unchanged(self):
        app=self.make();self.spawn(app,'preview_worker')
        metadata=dict(width=1920,height=1080,stride=3840,timestamp_seconds=time.monotonic(),_frame_rois=[(0,0,1920,1080)])
        app.preview_queue.put((metadata,b'',np.zeros((17,3)),0));self.wait(lambda:app.preview_version==1)
        overlay=np.frombuffer(app.preview_rgb[0],np.uint8).reshape(216,384,3)
        self.assertEqual(tuple(overlay[1,1]),(255,0,0));self.assertIsNone(app.environment_frame)
    def test_camera_tool_requests_overlay_without_enabling_ink(self):
        app=self.make();self.spawn(app,'preview_worker')
        app.suite.configure(dict(active=True,mode='sword',menu=True,preview=True))
        metadata=dict(width=1920,height=1080,stride=3840,timestamp_seconds=time.monotonic(),_frame_rois=[(0,0,1920,1080)])
        app.preview_queue.put((metadata,b'',np.zeros((17,3)),0));self.wait(lambda:app.preview_version==1)
        self.assertFalse(app.suite.ink_allowed())
        app.suite.configure(dict(active=True,mode='sword',menu=True))
        app.preview_queue.put((metadata,b'',np.zeros((17,3)),0));time.sleep(.08)
        self.assertEqual(app.preview_version,1)
    def prepare_painter(self):
        app=self.make();app.suite.configure(dict(active=True,mode='ink',menu=False))
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        app.log_path=Path(self.temp.name)/'ink.jsonl';app.refresh_art=lambda:None;app.painted=[]
        def add(event,theme):app.painted.append(event['action']);return dict(accepted=True)
        app.renderer=SimpleNamespace(add=add);self.spawn(app,'paint_worker');return app
    def test_failed_circle_releases_queued_ink(self):
        app=self.prepare_painter();app.suite.control_active=True;epoch=app.suite.snapshot()['epoch']
        app.paint_queue.put((dict(action='A02'),'bamboo',0,epoch));time.sleep(.06)
        self.assertEqual(app.painted,[]);app.suite.control_active=False
        self.wait(lambda:app.painted==['A02'])
    def test_opening_menu_cancels_queued_ink(self):
        app=self.prepare_painter();app.suite.control_active=True;epoch=app.suite.snapshot()['epoch']
        app.paint_queue.put((dict(action='A01'),'mixed',0,epoch));time.sleep(.06)
        app.suite.configure(dict(menu=True));time.sleep(.08);self.assertEqual(app.painted,[])
        app.suite.configure(dict(menu=False));time.sleep(.04);self.assertEqual(app.painted,[])

if __name__=='__main__':unittest.main()
