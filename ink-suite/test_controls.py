import ast,json,math,sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from gesture_menu import DwellSelector,point,MODES,ZONES
from suite_control import SuiteControl,MODES as BACKEND_MODES

def pose(cursor=(.25,.35)):
    p=[[.5,.5,1.] for _ in range(17)]
    p[5]=[.6,.4,1.];p[6]=[.4,.4,1.]
    scale=.2*16/9
    x=.5-(cursor[0]-.5)*scale*3.2/(16/9)
    y=.4+(cursor[1]-.35)*scale*2.6
    p[10]=[x,y,1.];p[9]=[.65,.8,1.]
    return p

class DwellTests(unittest.TestCase):
    def test_initial_menu_ignores_left_hand_and_arms_on_right_movement(self):
        d=DwellSelector();d.open(None,0.)
        p=pose();p[9]=[.62,.86,1.];p[10]=[.39,.87,1.]
        def update(t):return d.update(dict(pid=1,sequence=round(t*1000),timestamp_seconds=t,points17=p),t)
        initial=update(.1);self.assertEqual(d.hand,'right');self.assertFalse(d.armed)
        p[9]=[.62,.15,1.]
        result=update(.6);self.assertEqual(result['cursor'],initial['cursor']);self.assertFalse(d.armed)
        p[10]=[.37,.42,1.]
        result=update(.7);self.assertTrue(d.armed);self.assertIsNotNone(result['cursor'])
    def test_menu_uses_right_hand_regardless_of_circle_metadata(self):
        d=DwellSelector();d.open('left',0.)
        p=pose();p[9]=[.7,.1,1.];p[10]=[.37,.42,1.]
        d.update(dict(pid=1,sequence=1,timestamp_seconds=.6,points17=p),.6)
        self.assertEqual(d.hand,'right')
    def test_invalid_left_wrist_does_not_block_selection(self):
        d=self.make();p=pose();p[9]=[float('nan'),float('nan'),0]
        result=d.update(dict(pid=1,sequence=1,timestamp_seconds=.6,points17=p),.6)
        self.assertIsNotNone(result['cursor']);self.assertEqual(d.hand,'right')
    def make(self):
        d=DwellSelector();d.open('right',0.,pose((.5,.5)))
        return d
    def update(self,d,t,xy=(.25,.35),pid=1):
        return d.update(dict(pid=pid,sequence=round(t*1000),timestamp_seconds=t,points17=pose(xy)),t)
    def test_each_region_needs_two_seconds(self):
        for (x0,y0,x1,y1),name in zip(ZONES,MODES):
            xy=((x0+x1)/2,(y0+y1)/2)
            d=self.make();events=[]
            for i in range(85):
                t=.5+i/30;r=self.update(d,t,xy)
                if r['selected']:events.append((t,r['selected']))
            self.assertEqual(len(events),1);self.assertEqual(events[0][1],name);self.assertGreaterEqual(events[0][0],2.5)
    def test_frozen_frames_cannot_confirm(self):
        d=self.make();self.update(d,.5)
        state=dict(pid=1,sequence=500,timestamp_seconds=.5,points17=pose())
        for t in (.6,.8,1.,2.,4.):self.assertIsNone(d.update(state,t)['selected'])
        self.assertIsNone(d.since)
    def test_leave_or_tracking_loss_resets_timer(self):
        d=self.make()
        for i in range(50):self.update(d,.5+i/30)
        self.update(d,2.2,(.345,.55));r=self.update(d,2.3)
        self.assertEqual(r['progress'],0)
        s=dict(pid=1,sequence=2400,timestamp_seconds=2.4,points17=[])
        self.assertEqual(d.update(s,2.4)['progress'],0)
    def test_new_server_and_long_gap_reset(self):
        d=self.make()
        for i in range(50):self.update(d,.5+i/30)
        self.assertEqual(self.update(d,2.2,pid=2)['progress'],0)
        self.assertEqual(self.update(d,4.,pid=2)['progress'],0)
    def test_no_selection_at_opening_gesture_endpoint(self):
        d=DwellSelector();d.open('right',0,pose())
        for i in range(100):self.assertIsNone(self.update(d,i/30)['selected'])
    def test_bad_points(self):
        for points in [None,[],[['x',0,1]],[[float('nan'),0,1]]]:self.assertIsNone(point(points,0))
    def test_lowered_hands_cancel(self):
        d=self.make()
        for i in range(25):
            p=pose();p[9]=p[10]=[.5,.99,1.]
            r=d.update(dict(pid=1,sequence=i,timestamp_seconds=i/30,points17=p),i/30)
            if r['cancel']:break
        self.assertTrue(r['cancel'])

class SuiteTests(unittest.TestCase):
    def test_circle_diagnostic_survives_open_and_snapshot_is_independent(self):
        from test_circle_detector import pose as circular_pose
        control=SuiteControl()
        self.assertEqual(control.snapshot()['circle_diagnostic']['reason'],'inactive')
        control.configure(dict(active=True,mode='sword',menu=False))
        for i in range(121):
            result=control.observe(circular_pose(math.tau*2*i/120),i/30,1920,1080)
            if result['menu']:break
        self.assertTrue(result['menu']);self.assertEqual(result['open_serial'],1)
        self.assertEqual(result['circle_diagnostic']['reason'],'two_circles')
        self.assertEqual(result['circle_diagnostic']['hand'],'right')
        self.assertLess(len(json.dumps(result['circle_diagnostic'])),256)
        result['circle_diagnostic']['reason']='tampered'
        self.assertEqual(control.snapshot()['circle_diagnostic']['reason'],'two_circles')
        control.configure(dict(menu=False))
        self.assertEqual(control.snapshot()['circle_diagnostic']['reason'],'collecting')
        control.configure(dict(active=False))
        self.assertEqual(control.snapshot()['circle_diagnostic']['reason'],'inactive')

    def test_current_modes_match_menu_and_backend(self):
        tree=ast.parse((Path(__file__).parent/'menu_view.py').read_text(encoding='utf-8'))
        from radial_layout import MODES as menu_modes
        self.assertTrue(any(isinstance(n,ast.ImportFrom) and n.module=='radial_layout' and
                            any(a.name=='MODES' for a in n.names) for n in tree.body))
        self.assertEqual(MODES,('ink','sword','fight','fruit','taiko','platformer','runner','pal'))
        self.assertEqual(MODES,menu_modes)
        # Both PS games reuse the proven nonpainting pose/circle profile.
        # Their menu, child and visitor score identities remain distinct.
        self.assertEqual(tuple(m for m in MODES if m not in ('platformer','runner','pal')),BACKEND_MODES)
        control=SuiteControl()
        for mode in MODES:
            profile='sword' if mode in ('platformer','runner','pal') else mode
            result=control.configure(dict(active=True,mode=profile,menu=False))
            self.assertEqual(result['mode'],profile);self.assertEqual(control.ink_allowed(),mode=='ink')
    def test_sword_can_resync_after_backend_restart(self):
        control=SuiteControl()
        for menu in (False,True,False):
            result=control.configure(dict(active=True,mode='sword',menu=menu))
            self.assertEqual(result['mode'],'sword');self.assertEqual(result['menu'],menu)
            self.assertFalse(control.ink_allowed())
    def test_unconfirmed_circle_defers_paint_without_losing_action_history(self):
        s=SuiteControl();s.configure(dict(active=True,mode='ink',menu=False));epoch=s.snapshot()['epoch']
        class FakeDetector:
            result=dict(open=False,control_active=True,hand='right')
            def update(self,*args):return self.result
        s.detector=FakeDetector()
        s.observe([],1,1920,1080)
        self.assertEqual(s.snapshot()['epoch'],epoch);self.assertTrue(s.ink_allowed(epoch));self.assertTrue(s.paint_deferred(epoch))
        s.detector.result=dict(open=False,control_active=False,hand=None)
        s.observe([],2,1920,1080)
        self.assertEqual(s.snapshot()['epoch'],epoch);self.assertTrue(s.ink_allowed(epoch));self.assertFalse(s.paint_deferred(epoch))
        s.detector.result=dict(open=True,control_active=True,hand='right')
        s.observe([],3,1920,1080)
        self.assertFalse(s.ink_allowed(epoch));self.assertFalse(s.paint_deferred(epoch));self.assertTrue(s.snapshot()['menu'])
    def test_only_ink_can_draw_and_old_epoch_never_replays(self):
        s=SuiteControl();self.assertTrue(s.ink_allowed())
        old=s.snapshot()['epoch'];s.configure(dict(active=True,mode='fight',menu=False))
        self.assertFalse(s.ink_allowed());self.assertFalse(s.ink_allowed(old))
        for mode in ('ink','sword','environment','fruit'):
            s.configure(dict(mode=mode,menu=True));self.assertFalse(s.ink_allowed())
        s.configure(dict(mode='ink',menu=False));self.assertTrue(s.ink_allowed());self.assertFalse(s.ink_allowed(old))
    def test_disable_restores_standalone_ink(self):
        s=SuiteControl();s.configure(dict(active=True,mode='sword',menu=True));s.configure(dict(active=False))
        self.assertTrue(s.ink_allowed());self.assertFalse(s.snapshot()['menu']);self.assertEqual(s.snapshot()['mode'],'ink')
    def test_invalid_command_does_not_mutate(self):
        s=SuiteControl();old=s.snapshot()
        for c in [dict(mode='bad'),dict(menu='false'),dict(active=1)]:
            with self.assertRaises(ValueError):s.configure(c)
            self.assertEqual(old,s.snapshot())

if __name__=='__main__':unittest.main()
