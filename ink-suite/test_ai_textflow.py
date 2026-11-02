import unittest
from ai_textflow import paginate,continuous_lines,utf8_line_starts,Reveal,ScrollWindow


class TextFlowTests(unittest.TestCase):
    def test_utf8_newlines_spaces_and_emoji_are_partitioned_without_loss(self):
        text='山水与🌸\n\nDeepSeek   完整回答\n最后一行'
        starts=(0,text.index('DeepSeek'),text.index('最后'))
        encoded=tuple(len(text[:n].encode('utf8')) for n in starts)
        self.assertEqual(utf8_line_starts(text,encoded),starts)
        pages=paginate(text,starts,2)
        self.assertEqual(''.join(text[p.start:p.end] for p in pages),text)
        self.assertEqual(len(pages),2)

    def test_reveal_is_monotonic_configurable_and_immediate_when_requested(self):
        reveal=Reveal(60);reveal.begin(100,10)
        self.assertTrue(reveal.advance(10.5));self.assertEqual(reveal.shown,30)
        self.assertFalse(reveal.advance(10.4));self.assertEqual(reveal.shown,30)
        self.assertTrue(reveal.finish());self.assertFalse(reveal.active)
        reveal.begin(100,20,False);self.assertFalse(reveal.active)
        with self.assertRaises(ValueError):Reveal(0)

    def test_invalid_line_offsets_cannot_silently_drop_content(self):
        for starts in ((1,),(0,0),(0,9)):
            with self.assertRaises(ValueError):paginate('完整文本',starts,2)
        with self.assertRaises(ValueError):utf8_line_starts('山水',(0,1))

    def test_continuous_lines_reconstruct_all_characters_without_pages(self):
        text='完整的山水\n\n与🌸一起\n最后一行'
        starts=(0,6,7,12)
        lines=continuous_lines(text,starts)
        self.assertEqual(''.join(text[line.start:line.end] for line in lines),text)

    def test_scroll_is_bounded_smooth_and_manual_motion_disables_follow(self):
        scroll=ScrollWindow();scroll.reset(True);scroll.set_limit(300)
        scroll.move(84);self.assertFalse(scroll.follow);self.assertEqual(scroll.offset,0)
        scroll.tick(.04);self.assertGreater(scroll.offset,0);self.assertLess(scroll.offset,84)
        for _ in range(40):scroll.tick(.04)
        self.assertEqual(scroll.offset,84);self.assertFalse(scroll.moving)
        scroll.move(10000);self.assertEqual(scroll.target,300)
        scroll.set_limit(10);self.assertEqual(scroll.offset,10);self.assertEqual(scroll.target,10)
        scroll.reset(True);scroll.move(0);self.assertFalse(scroll.follow)


if __name__=='__main__':unittest.main()
