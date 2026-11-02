"""A connected idle controller cannot steal the health gesture cursor."""
import threading,time,unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from gamepad.host import HostControls

class HealthGamepadTests(unittest.TestCase):
    def host(self,pressed=()):
        view=NS(choices=('health_start_learn','health_home'),rectangles=((.1,.1,.4,.8),(.6,.8,.9,.95)),
            gesture_navigation=True,set_gamepad_focus=Mock(),action=Mock())
        host=HostControls.__new__(HostControls)
        host.suite=NS(stop=threading.Event(),control_mode='body',menu_open=False,health_active=True,health_view=view)
        host.reader=NS(snapshot=lambda:dict(connected=True,name='Synthetic G7',pressed=pressed,released=(),buttons=pressed,axes={}))
        host.pointer=NS(update=lambda *_,**__: {})
        host.navigation=NS(update=Mock(return_value=1));host.dialog=lambda:None
        host.closed=False;host.last=time.monotonic();host.navigation_until=0.;host.connected=False
        return host,view
    def test_idle_connected_pad_does_not_clear_health_body_selection(self):
        host,view=self.host();self.assertTrue(host.tick())
        view.set_gamepad_focus.assert_not_called();host.navigation.update.assert_not_called()
    def test_recent_direction_owns_focus(self):
        host,view=self.host(('RIGHT',));self.assertTrue(host.tick())
        view.set_gamepad_focus.assert_called_once_with(1)
    def test_menu_edge_still_pauses_without_prior_navigation(self):
        host,view=self.host(('START',));self.assertTrue(host.tick())
        view.action.assert_called_once_with('health_pause')

if __name__=='__main__':unittest.main()
