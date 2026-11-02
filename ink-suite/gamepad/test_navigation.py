import unittest
from gamepad.navigation import Navigation

class NavigationTests(unittest.TestCase):
    def test_repeat_and_page_reset(self):
        n=Navigation();choices=('a','b','c');rects=((0,0,.2,.2),(.4,0,.6,.2),(.8,0,1,.2))
        self.assertEqual(n.update({'buttons':{'RIGHT'}},choices,rects,1,0),1)
        self.assertEqual(n.update({'buttons':{'RIGHT'}},choices,rects,1,.1),1)
        self.assertEqual(n.update({'buttons':{'RIGHT'}},choices,rects,1,.4),2)
        self.assertEqual(n.update({},choices,rects,2,.5),0)
    def test_grid_and_empty(self):
        n=Navigation();self.assertIsNone(n.update({},(),(),1,0))
        r=((0,0,.2,.2),(.4,0,.6,.2),(0,.5,.2,.7))
        self.assertEqual(n.update({'axes':{'ly':1}},('a','b','c'),r,2,0),2)
    def test_short_tap_survives_release_before_menu_tick(self):
        n=Navigation();r=((0,0,.2,.2),(.4,0,.6,.2))
        self.assertEqual(n.update({'buttons':set(),'pressed':{'RIGHT'}},('a','b'),r,1,0),1)

if __name__=='__main__':unittest.main()
