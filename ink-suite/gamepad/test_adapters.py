"""No devices/cameras/speakers: deterministic actual game input contracts."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from gamepad.adapters import GamepadControls,create_gamepad


class Reader:
    def __init__(self):
        self.seq=0;self.s=dict(connected=True,buttons=set(),pressed=set(),axes={})
        self.close=Mock()
    def snapshot(self):
        return dict(self.s,sequence=self.seq,timestamp=100+self.seq*.01)
    def set(self,buttons=(),pressed=(),**axes):
        self.seq+=1;self.s=dict(connected=True,buttons=set(buttons),pressed=set(pressed),axes=axes)


class ControlsTests(unittest.TestCase):
    def make(self,mode):
        r=Reader();g=GamepadControls(mode,r);self.assertTrue(g.sample(100)['valid']);return g,r
    def test_default_body_does_not_create_a_reader(self):
        with patch.dict('os.environ',{},clear=True):self.assertIsNone(create_gamepad('runner'))
    def test_reconnect_held_action_requires_release(self):
        g,r=self.make('runner');r.s['connected']=False
        self.assertFalse(g.runner(101)['valid'])
        r.set(('A',));self.assertFalse(g.runner(102)['valid'])
        r.set();self.assertFalse(g.runner(103)['jump'])
        r.set(('A',));self.assertTrue(g.runner(104)['jump'])
    def test_pause_reset_requires_release_and_drops_stale_edges(self):
        g,r=self.make('runner');g.reset();r.set(('A',),('A',))
        self.assertFalse(g.runner(101)['valid']);r.set()
        self.assertTrue(g.runner(102)['valid']);self.assertFalse(g.runner(102)['jump'])
    def test_runner_lanes_are_discrete_with_neutral_rearm(self):
        g,r=self.make('runner');r.set(lx=1.)
        self.assertEqual(g.runner(101)['move'],1);self.assertEqual(g.runner(102)['move'],0)
        r.set(lx=0.);g.runner(103);r.set(lx=-1.)
        self.assertEqual(g.runner(104)['move'],-1)
    def test_controller_start_is_reserved_for_host(self):
        g,r=self.make('runner');r.set(('START',),('START',))
        c=g.runner(101);self.assertFalse(c['confirm']);self.assertNotIn('START',c['pressed'])
    def test_short_direction_press_and_release_survives_one_tick(self):
        g,r=self.make('runner');r.set(pressed=('RIGHT',))
        self.assertEqual(g.runner(101)['move'],1)
        r.set();self.assertEqual(g.runner(102)['move'],0)
    def test_duplicate_sequence_does_not_repeat_drum(self):
        g,r=self.make('taiko');r.set(('X','A'),('X','A'))
        self.assertEqual([e['hand'] for e in g.taiko(101)['events']],['L','L','R'])
        self.assertEqual(g.taiko(101.01)['events'],[])
    def test_drum_buttons_and_event_timestamps(self):
        g,r=self.make('taiko');r.set(('X','B','Y','A'))
        c=g.taiko(101)
        self.assertEqual([e['hand'] for e in c['events']],['L','R','C','L','R'])
        self.assertEqual(len({e['id'] for e in c['events']}),5)
        self.assertTrue(all(100.9<=e['timestamp']<=101 for e in c['events']))
    def test_platformer_held_jump_follows_engine_level_contract(self):
        g,r=self.make('platformer');r.set(('A',),lx=.9)
        self.assertTrue(g.platformer(101)['jump']);self.assertTrue(g.platformer(101.02)['jump'])
        r.set();self.assertFalse(g.platformer(102)['jump'])
    def test_fight_uses_bounded_edges_and_no_right_stick(self):
        g,r=self.make('fight');r.set(('X','B','Y','RB','LB','A'),lx=-1.,ry=-1.)
        c=g.fight(101)
        self.assertEqual(c['moves'],['left','kick_left','palm','special'])
        self.assertTrue(c['guard']);self.assertTrue(c['jump']);self.assertEqual(c['move_axis'],-1.)
        self.assertEqual(g.fight(101.02)['moves'],[])
    def test_fruit_slice_requires_trigger_or_a_and_position_is_bounded(self):
        g,r=self.make('fruit');r.set(lx=1.,ly=-1.)
        c=g.fruit(101);self.assertFalse(c['slicing'])
        r.set(rt=.8,lx=1.,ly=-1.)
        for i in range(100):c=g.fruit(102+i*.02)
        self.assertTrue(c['slicing']);self.assertEqual(c['x'],1.);self.assertEqual(c['y'],0.)
    def test_ink_selects_stroke_without_pose_fields(self):
        g,r=self.make('ink');r.set(('RB','A'),rt=.8)
        c=g.ink(101);self.assertEqual(c['action'],'A02');self.assertTrue(c['draw']);self.assertEqual(c['speed'],.8)
        self.assertFalse(any('pose' in k or 'joint' in k for k in c))
    def test_nan_axes_cannot_move_or_become_a_speed(self):
        g,r=self.make('ink');r.set(lx=float('nan'),rt=float('inf'))
        c=g.ink(101);self.assertEqual(c['lx'],0.);self.assertEqual(c['speed'],.08)
    def test_close_is_idempotent_and_never_reads_again(self):
        g,r=self.make('runner');g.close();g.close();r.close.assert_called_once()
        r.snapshot=Mock(side_effect=AssertionError('closed reader'))
        self.assertFalse(g.runner(101)['valid'])
    def test_real_evdev_feed_contract_with_generic_g7_axes(self):
        from gamepad.device import GamepadReader,EV_KEY,EV_SYN,SYN_REPORT,EV_ABS
        r=GamepadReader(start=False,clock=lambda:100.)
        axes={code:(128 if code in (0,1,2,5) else 0,0,255,0,0,0) for code in (0,1,2,5,9,10)}
        r.attach('GameSir G7 Pro',axes)
        g=GamepadControls('runner',r);self.assertTrue(g.runner(100)['valid'])
        r.feed(EV_KEY,304,1);r.feed(EV_SYN,SYN_REPORT,0)
        self.assertTrue(g.runner(100.01)['jump']);self.assertFalse(g.runner(100.02)['jump'])
        r.feed(EV_ABS,0,255);r.feed(EV_SYN,SYN_REPORT,0)
        self.assertEqual(g.runner(100.03)['move'],1)
        g.close()


if __name__=='__main__':unittest.main()
