import unittest
from item_effects import ItemEffects

class Tests(unittest.TestCase):
    def event(self,kind='double'):return dict(kind=kind,x=500,y=300,delta=10)
    def test_only_special_items(self):
        fx=ItemEffects();fx.trigger(self.event('normal'));self.assertEqual(fx.bounds(),[])
    def test_refresh_and_bound(self):
        fx=ItemEffects()
        for _ in range(100):
            for k in ('double','ice','bomb'):fx.trigger(self.event(k))
        self.assertEqual(len(fx.items),3);self.assertEqual(len(fx.bounds()),4)
    def test_pause_expiry_and_cleanup(self):
        fx=ItemEffects();fx.trigger(self.event());fx.advance(0);self.assertEqual(fx.items[0]['age'],0)
        fx.advance(1.21);self.assertEqual(fx.bounds(),[])
    def test_event_unchanged(self):
        fx=ItemEffects();e=self.event('bomb');e['delta']=-7;before=dict(e);fx.trigger(e);fx.advance(.1)
        self.assertEqual(e,before);self.assertEqual(fx.items[0]['delta'],-7);fx.clear();self.assertFalse(fx.items)

if __name__=='__main__':unittest.main()
