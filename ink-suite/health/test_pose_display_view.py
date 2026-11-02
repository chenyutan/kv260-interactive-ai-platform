import unittest
import cairo
from . import view
from .test_engine import person


class DrawingProbe:
    def __init__(self):
        self.surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,960,540)
        self.context=cairo.Context(self.surface)
        self.dashes=[]
    def __getattr__(self,name):return getattr(self.context,name)
    def set_dash(self,value):self.dashes.append(value);self.context.set_dash(value)


class DisplayDrawingTests(unittest.TestCase):
    def test_uncertain_observation_uses_dashed_connection(self):
        points=person();points[9][2]=.1
        cr=DrawingProbe()
        view.HealthView._body(None,cr,points,(653,161,913,313))
        self.assertIn((4.,3.),cr.dashes)
        self.assertEqual(cr.dashes[-1],())

    def test_reliable_observation_remains_solid(self):
        cr=DrawingProbe()
        view.HealthView._body(None,cr,person(),(653,161,913,313))
        self.assertEqual(cr.dashes,[])

    def test_actual_projection_clips_to_its_panel_and_restores_context(self):
        cr=DrawingProbe();before=cr.clip_extents()
        points=person();points[9]=[0.,0.,.9]
        view.HealthView._body(None,cr,points,(653,161,913,313))
        self.assertEqual(cr.clip_extents(),before)


if __name__=='__main__':unittest.main()
