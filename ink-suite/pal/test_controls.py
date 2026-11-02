import unittest
from pal.controls import BodyKeys,PadKeys

def pose(sequence=1,t=10.,right=(.40,.62),left=(.60,.62)):
    points=[[.5,.5,0] for _ in range(17)]
    for i,p in {5:(.60,.35),6:(.40,.35),9:left,10:right}.items():points[i]=[*p,.8]
    return dict(pid=7,sequence=sequence,timestamp_seconds=t,points17=points)

class BodyTests(unittest.TestCase):
    def test_initial_raise_cannot_confirm(self):
        c=BodyKeys();self.assertEqual(c.update(pose(left=(.60,.25)),10)['pulses'],set())
    def test_release_then_confirmation_is_edge(self):
        c=BodyKeys();c.update(pose(),10)
        self.assertEqual(c.update(pose(2,10.1,left=(.60,.25)),10.1)['pulses'],{'Return'})
        self.assertEqual(c.update(pose(3,10.15,left=(.60,.25)),10.15)['pulses'],set())
    def test_right_hand_mirrors_world_direction(self):
        c=BodyKeys();self.assertEqual(c.update(pose(right=(.22,.55)),10)['held'],{'Right'})
    def test_timeout_releases_direction(self):
        c=BodyKeys();p=pose(right=(.22,.55));c.update(p,10)
        self.assertFalse(c.update(p,10.3)['valid'])
    def test_duplicate_cannot_repeat_confirmation(self):
        c=BodyKeys();c.update(pose(),10);p=pose(2,10.1,left=(.60,.25));c.update(p,10.1)
        self.assertEqual(c.update(p,10.11)['pulses'],set())
    def test_nan_stale_and_out_of_order_rejected(self):
        c=BodyKeys();p=pose();c.update(p,10)
        self.assertFalse(c.update(pose(0,9.9),10)['valid'])
        p=pose(2,10.1);p['points17'][10][0]=float('nan')
        self.assertFalse(c.update(p,10.1)['valid'])
        self.assertFalse(c.update(pose(3,11),12)['valid'])

class PadTests(unittest.TestCase):
    def test_quick_released_confirm_and_menu_edges_are_kept(self):
        c=PadKeys();c.update(dict(connected=True),1)
        self.assertEqual(c.update(dict(connected=True,buttons=set(),pressed={'A'}),1.1)['pulses'],{'Return'})
        self.assertEqual(c.update(dict(connected=True,buttons=set(),pressed={'Y'}),1.2)['pulses'],{'Escape'})
    def test_entry_consumes_held_a_then_release_rearms(self):
        c=PadKeys();self.assertEqual(c.update(dict(connected=True,buttons={'A'}),1)['pulses'],set())
        c.update(dict(connected=True),1.1)
        self.assertEqual(c.update(dict(connected=True,buttons={'A'}),1.2)['pulses'],{'Return'})
    def test_dpad_stick_and_action_mapping(self):
        c=PadKeys();c.update(dict(connected=True),1)
        out=c.update(dict(connected=True,axes={'lx':-.9},buttons={'B','LB'}),1.1)
        self.assertEqual(out['held'],{'Left'});self.assertEqual(out['pulses'],{'Escape','Page_Up'})
    def test_host_buttons_are_never_game_keys(self):
        c=PadKeys();c.update(dict(connected=True),1)
        out=c.update(dict(connected=True,axes={'rx':1},buttons={'START','BACK','R3'}),1.1)
        self.assertEqual(out['held']|out['pulses'],set())
    def test_disconnect_releases_and_rearms_safely(self):
        c=PadKeys();c.update(dict(connected=True),1)
        self.assertFalse(c.update(dict(connected=False),1.1)['valid'])
        self.assertEqual(c.update(dict(connected=True,buttons={'A'}),1.2)['pulses'],set())
    def test_future_packet_and_opposing_dpad(self):
        c=PadKeys()
        self.assertFalse(c.update(dict(connected=True,timestamp=3),2)['valid'])
        self.assertEqual(c.update(dict(connected=True,buttons={'LEFT','RIGHT'}),2)['held'],set())
    def test_evdev_idle_holds_are_not_a_stale_pose(self):
        c=PadKeys();c.update(dict(connected=True,timestamp=1),1)
        self.assertEqual(c.update(dict(connected=True,timestamp=1,axes={'lx':1}),10)['held'],{'Right'})
    def test_initial_held_stick_and_dpad_require_neutral_before_walking(self):
        for packet in (dict(axes={'lx':1}),dict(buttons={'LEFT'}),dict(axes={'ly':-.9})):
            c=PadKeys();out=c.update(dict(connected=True,**packet),1)
            self.assertFalse(out['valid']);self.assertEqual(out['held']|out['pulses'],set())
            self.assertEqual(out['reason'],'controller_release_required')
            self.assertTrue(c.update(dict(connected=True),1.1)['valid'])
            self.assertTrue(c.update(dict(connected=True,**packet),1.2)['held'])
    def test_disconnect_and_owner_reset_each_require_controls_released(self):
        c=PadKeys();c.update(dict(connected=True),1)
        c.update(dict(connected=False),1.1)
        self.assertFalse(c.update(dict(connected=True,axes={'lx':.8}),1.2)['valid'])
        c.update(dict(connected=True),1.3)
        c.reset()
        self.assertFalse(c.update(dict(connected=True,buttons={'A'},pressed={'A'}),1.4)['valid'])
        c.update(dict(connected=True),1.5)
        self.assertEqual(c.update(dict(connected=True,pressed={'Y'}),1.6)['pulses'],{'Escape'})
    def test_queued_short_press_cannot_skip_first_neutral_observation(self):
        c=PadKeys()
        self.assertFalse(c.update(dict(connected=True,pressed={'A'}),1)['valid'])
        c.update(dict(connected=True),1.1)
        self.assertEqual(c.update(dict(connected=True,pressed={'A'}),1.2)['pulses'],{'Return'})

if __name__=='__main__':unittest.main()
