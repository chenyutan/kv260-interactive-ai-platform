import importlib.util
from pathlib import Path
import unittest

from fruit.input_gate import consume_pose

source=Path(__file__).resolve().parents[2]/'fruit-slice/native/engine.py'
if not source.is_file():source=Path('/home/ubuntu/fruit-slice/native/engine.py')
spec=importlib.util.spec_from_file_location('fruit_original_engine_for_gate_test',source)
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)

def packet(sequence,x=.20,**extra):
    points=[[.5,.5,.9] for _ in range(17)]
    points[9]=[x,.5,.8];points[10]=[.8,.5,.8]
    result=dict(pid=42,sequence=sequence,timestamp_seconds=10+sequence/30,
                phase='unknown',points17=points,suite={'active':True,'mode':'fruit','menu':False,'control_active':False})
    result.update(extra);return result

class FruitInputGateTests(unittest.TestCase):
    def setUp(self):
        self.game=engine.Game();self.game.start(7);self.game.state='playing'
        self.consumer=engine.PoseInput(self.game);self.consumer.mirror=False

    def test_circle_candidate_keeps_clock_moving_without_cutting(self):
        self.game.advance(.03);consume_pose(self.consumer,packet(1))
        obj=self.game.spawn('normal',x=400,y=360,vx=0,vy=0)
        before=self.game.elapsed
        for i in range(2,8):
            self.game.advance(1/30)
            blocked=packet(i,x=.60,suite={'active':True,'mode':'fruit','menu':False,'control_active':True})
            self.assertEqual(consume_pose(self.consumer,blocked),[])
        self.assertEqual(self.game.state,'playing')
        self.assertGreater(self.game.elapsed,before+.15)
        self.assertIn(obj,self.game.objects);self.assertEqual(self.game.score,0)

    def test_recovery_does_not_cut_across_gap(self):
        self.game.advance(.03);consume_pose(self.consumer,packet(1,x=.1))
        obj=self.game.spawn('normal',x=500,y=360,vx=0,vy=0)
        self.game.advance(.03);consume_pose(self.consumer,None)
        self.game.advance(.03);self.assertTrue(consume_pose(self.consumer,packet(3,x=.8)))
        self.assertIn(obj,self.game.objects);self.assertEqual(self.game.score,0)

    def test_low_person_confidence_does_not_disable_valid_wrist_input(self):
        self.game.advance(.03)
        state=packet(1,quality='关键关节不完整',quality_detail={'valid':False})
        self.assertEqual(len(consume_pose(self.consumer,state)),2)

    def test_explicit_disconnect_wrong_mode_and_menu_never_cut(self):
        for extra in ({'camera_link':{'present':False}},{'phase':'lost'},
                      {'suite':{'active':True,'mode':'fight'}},{'suite':{'active':True,'menu':True}}):
            self.assertEqual(consume_pose(self.consumer,packet(1,**extra)),[])
        self.assertEqual(self.game.state,'playing')

if __name__=='__main__':unittest.main()
