"""Source and local visitor resets; legacy single-player metadata is inert."""
import copy,unittest
from player_context import input_context,context_key

def packet(**fields):
    lease=dict(identity='owner-a',epoch=3,input_allowed=True,locked=True);lease.update(fields)
    return dict(pid=10,sequence=9,points17=[[.5,.5,.9] for _ in range(17)],player_tracking=lease)

class PlayerContextTests(unittest.TestCase):
    def test_tracking_epoch_and_person_ids_do_not_reset_navigation(self):
        s=packet();_,key,_=input_context(s)
        next=packet(identity='other',epoch=99,input_allowed=False);next.update(person_id='other',track_id='other-track')
        out,_,changed=input_context(next,key)
        self.assertFalse(changed);self.assertIs(out,next);self.assertNotIn('motion_history_discontinuity',out)
    def test_stable_source_does_not_reset_navigation_on_poll(self):
        s=packet();_,key,_=input_context(s);out,_,changed=input_context(s,key)
        self.assertFalse(changed);self.assertIs(out,s)
    def test_denied_tracking_does_not_mask_or_change_original_packet(self):
        s=packet(input_allowed=False);s['available']=True;before=copy.deepcopy(s);out,_,_=input_context(s)
        self.assertEqual(s,before);self.assertTrue(out['available']);self.assertIs(out['points17'],s['points17'])
    def test_malformed_tracking_is_ignored_and_context_is_always_allowed(self):
        for invalid in (None,[],{},dict(identity='',epoch=1,input_allowed=False),dict(identity='a',epoch=True)):
            with self.subTest(invalid=invalid):
                s=packet();s['player_tracking']=invalid;out,_,_=input_context(s)
                self.assertEqual(out['points17'],s['points17']);self.assertNotIn('available',out)
                self.assertEqual(context_key(s),((10,None),True))
    def test_explicit_local_visitor_is_attached_by_shallow_copy(self):
        s=packet();before=copy.deepcopy(s);out,key,_=input_context(s,visitor_session='visitor-a')
        self.assertIsNot(out,s);self.assertEqual(s,before);self.assertEqual(out['visitor_session'],'visitor-a')
        self.assertIs(out['points17'],s['points17']);self.assertEqual(key,(10,'visitor-a'));self.assertEqual(context_key(out),(key,True))
    def test_local_visitor_change_marks_history_discontinuity(self):
        s=packet();_,key,_=input_context(s,visitor_session='visitor-a')
        out,next_key,changed=input_context(s,key,visitor_session='visitor-b')
        self.assertTrue(changed);self.assertTrue(out['motion_history_discontinuity'])
        self.assertEqual(next_key,(10,'visitor-b'));self.assertEqual(out['points17'],s['points17'])
    def test_backend_pid_change_marks_history_even_without_tracking_metadata(self):
        s=dict(pid=2,points17=[]);_,key,_=input_context(s)
        out,next_key,changed=input_context(dict(pid=3,points17=[]),key)
        self.assertTrue(changed);self.assertTrue(out['motion_history_discontinuity']);self.assertEqual(next_key,(3,None))
    def test_real_available_false_is_preserved_without_becoming_lease_gate(self):
        s=packet();s['available']=False;out,_,_=input_context(s)
        self.assertFalse(out['available']);self.assertEqual(out['points17'],s['points17']);self.assertTrue(context_key(out)[1])
    def test_missing_source_has_no_player_gate(self):
        self.assertEqual(context_key({}),(None,True));self.assertEqual(context_key(None),(None,True))
        self.assertEqual(input_context(None,('previous',)),(None,('previous',),False))

if __name__=='__main__':unittest.main()