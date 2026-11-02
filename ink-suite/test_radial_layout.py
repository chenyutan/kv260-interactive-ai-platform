import math
import unittest
from gesture_menu import DwellSelector, ZONES
from radial_layout import MODES, geometry, rectangles, hit_index, observed_pose
from test_controls import pose


class RadialTests(unittest.TestCase):
    def test_five_real_circles_fit_screen_and_share_one_orbit(self):
        for w,h in ((960,540),(1280,720),(1920,1080),(640,480),(640,420)):
            (cx,cy,_),circles=geometry(w,h)
            self.assertEqual(len(circles),len(MODES))
            distances=[math.hypot(x-cx,y-cy) for x,y,r in circles]
            self.assertLess(max(distances)-min(distances),1e-8)
            for i,(x,y,r) in enumerate(circles):
                self.assertTrue(0<=x-r<x+r<=w and 0<=y-r<y+r<h*.915)
                self.assertEqual(hit_index(x/w,y/h,w,h),i)
                self.assertIsNone(hit_index((x+r*.95)/w,(y+r*.95)/h,w,h))
            self.assertEqual(hit_index(cx/w,cy/h,w,h),len(MODES))
    def test_footer_and_unavailable_values(self):
        self.assertEqual(hit_index(.10,.525,visitors=True),len(MODES)+1)
        self.assertEqual(hit_index(.90,.525,visitors=True),len(MODES)+2)
        for p in ((None,.5),(float('nan'),.5),(.5,float('inf'))):self.assertIsNone(hit_index(*p))
    def test_dwell_follows_circle_not_its_bounding_corner(self):
        for i,(x,y,r) in enumerate(geometry()[1]):
            d=DwellSelector();d.set_choices(MODES,rectangles(),hit_test=hit_index)
            d.open(None,0,pose((.5,.5)));target=(x/960,y/540)
            # Reachable fixture body width keeps wrist within the camera frame.
            def sample(n,xy):
                p=pose(xy);p[5]=[.56,.4,1];p[6]=[.44,.4,1]
                scale=.12*16/9;p[10]=[.5-(xy[0]-.5)*scale*3.2/(16/9),.4+(xy[1]-.35)*scale*2.6,1]
                return dict(pid=1,sequence=n,timestamp_seconds=n/30,points17=p)
            results=[d.update(sample(n,target),n/30) for n in range(15,96)]
            self.assertEqual([r['selected'] for r in results if r['selected']],[MODES[i]])
            d.set_choices(MODES,rectangles(),hit_test=hit_index);d.open(None,0,pose((.5,.5)))
            corner=((x+r*.95)/960,(y+r*.95)/540)
            self.assertTrue(all(not d.update(sample(n,corner),n/30)['selected'] for n in range(15,96)))
    def test_switching_secondary_menu_clears_radial_hit_function(self):
        d=DwellSelector();d.set_choices(MODES,rectangles(),hit_test=hit_index)
        d.set_choices(('resume','return','exit'))
        self.assertIsNone(d.hit_test);self.assertEqual(d.zones,ZONES)
    def test_metrics_are_unmodified_observations(self):
        points,fps,latency,key=observed_pose(dict(pid=5,sequence=3,points17=[[.5,.5,.08]]*17,pose_fps=18.24,latency_ms=169.87))
        self.assertEqual((fps,latency),(18.24,169.87));self.assertEqual(sum(p is not None for p in points),17)
        self.assertEqual(observed_pose(dict(stale=True,pose_fps=99)),((),None,None,None))
        _,fps,latency,_=observed_pose(dict(pose_fps=float('nan'),latency_ms=-10));self.assertIsNone(fps);self.assertIsNone(latency)


if __name__=='__main__':unittest.main()
