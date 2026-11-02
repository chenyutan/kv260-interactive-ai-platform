import unittest
from cut_effects import CutEffects
class Tests(unittest.TestCase):
    def test_budget_expiry_pause(self):
        f=CutEffects()
        for _ in range(30):f.trigger(dict(kind='normal',fruit='apple',x=200,y=200,combo=3,cut_angle=1.2))
        self.assertEqual(len(f.items),5);self.assertEqual(f.items[0]['angle'],1.2)
        f.advance(0);self.assertEqual(f.items[0]['age'],0)
        f.advance(.6);self.assertEqual(f.bounds(),[])
    def test_specials_separate(self):
        f=CutEffects();f.trigger(dict(kind='bomb'));self.assertEqual(f.items,[])
