"""Real visitor SQLite snapshots, bounded multi-page PNGs and CJK glyphs."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock

from PIL import Image
from .store import VisitorStore
from .report_image import (MAX_HEIGHT, WIDTH, _core_stats, _date, _font, font_path,
                           render_report_page, render_report_pages, report_fingerprint,
                           report_manifest)


def synthetic_report(rounds=7):
    games=('sword','fight','fruit','taiko')
    rows=[]
    for i in range(rounds):
        game=games[i%4]
        data=dict(score=12800+i*937,best_combo=12+i,kills=31+i,graze=18+i,lives=3,
                  result='win',hp=72,enemy_hp=0,hits=34,perfect_blocks=5,blocks=11,
                  fruits_cut=87,bombs_hit=1,missed=2,bonus_score=650,
                  accuracy=96.7,rank='S',strikes=120,elapsed=88.2,
                  counts={'perfect':87,'great':26,'good':6,'miss':1})
        rows.append(dict(id=f'{i:032x}',game=game,
                         title={'sword':'御剑山河','fight':'墨武对决','fruit':'彩墨切水果','taiko':'鼓动山河'}[game],
                         rules='standard',rules_label='体感判定 · 完整曲目' if game=='taiko' else '标准玩法',
                         status='interrupted' if i==5 else 'active' if i==6 else 'completed',
                         started='2026-10-02T14:35:00+00:00',ended=None if i==6 else '2026-10-02T14:36:30+00:00',
                         score=12800+i*937,data=data))
    return dict(name='山河客 · 小雨',status='archived',started='2026-10-02T14:32:00+00:00',
                ended='2026-10-02T14:48:00+00:00',rounds=rows,artworks=[],
                ranking_note='不同游戏独立分榜；仅完成局入榜。')


class ReportImageTests(unittest.TestCase):
    def test_png_contains_real_all_round_ids_and_cjk_title(self):
        report=synthetic_report()
        before=copy.deepcopy(report)
        data=render_report_page(report)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format,'PNG')
            self.assertEqual(image.width,WIDTH)
            self.assertLessEqual(image.height,MAX_HEIGHT)
            metadata=json.loads(image.info['InkSuiteReport'])
            self.assertEqual(metadata['round_ids'],[r['id'] for r in report['rounds']])
            self.assertEqual(metadata['round_count'],7)
            self.assertIn('山河客',image.info['Title'])
            image.load()
        self.assertEqual(report,before)

    def test_zero_rounds_still_have_one_bounded_page(self):
        report=synthetic_report(0)
        manifest=report_manifest(report)
        self.assertEqual(manifest['page_count'],1)
        self.assertEqual(manifest['pages'][0]['round_ids'],[])
        with Image.open(io.BytesIO(render_report_page(report))) as image:
            self.assertLess(image.height,2000)
        with self.assertRaises(ValueError):render_report_page(report,2)

    def test_many_rounds_keep_all_records_in_order_across_lazy_pages(self):
        report=synthetic_report(43)
        manifest=report_manifest(report)
        self.assertEqual(manifest['page_count'],3)
        self.assertEqual([r for p in manifest['pages'] for r in p['round_ids']],
                         [r['id'] for r in report['rounds']])
        seen=[]
        for filename,png in render_report_pages(report):
            self.assertRegex(filename,r'^ink-report-\d{2}\.png$')
            with Image.open(io.BytesIO(png)) as image:
                self.assertLessEqual(image.height,MAX_HEIGHT)
                seen.extend(json.loads(image.info['InkSuiteReport'])['round_ids'])
        self.assertEqual(seen,[r['id'] for r in report['rounds']])

    def test_very_long_counter_details_force_safe_page_breaks(self):
        report=synthetic_report(13)
        for r in report['rounds']:
            r['game']='taiko'
            r['data']['counts']={('判定项目'+str(i)+'很长的说明文字'*4)[:40]:i for i in range(40)}
        manifest=report_manifest(report)
        self.assertGreater(manifest['page_count'],1)
        self.assertEqual(sum(len(p['round_ids']) for p in manifest['pages']),13)
        with Image.open(io.BytesIO(render_report_page(report))) as image:
            self.assertLessEqual(image.height,MAX_HEIGHT)

    def test_metrics_have_correct_percent_and_fight_outcome_separation(self):
        report=synthetic_report(4)
        fields=dict(_core_stats(report['rounds'][3]))
        self.assertEqual(fields['准确率'],'96.7%')
        self.assertEqual(fields['评价'],'S')
        self.assertEqual(fields['最佳连击'],'15')
        self.assertEqual(_date('2026-10-02T14:35:00+00:00'),'2026.10.02  22:35')

    def test_artwork_reader_receives_only_authorized_id_and_only_on_first_page(self):
        report=synthetic_report(21)
        art_id='abcdef0123456789'*2
        report['artworks']=[dict(id=art_id,title='合成作品',marks=3)]
        output=io.BytesIO();Image.new('RGB',(1280,720),'#7ba496').save(output,format='PNG')
        reader=Mock(return_value=output.getvalue())
        page=render_report_page(report,1,reader)
        reader.assert_called_once_with(art_id)
        with Image.open(io.BytesIO(page)) as image:self.assertLessEqual(image.height,MAX_HEIGHT)
        reader.reset_mock();render_report_page(report,2,reader);reader.assert_not_called()
        report['artworks'][0]['id']='../../secret.png'
        with self.assertRaises(ValueError):render_report_page(report,1,reader)
        reader.assert_not_called()

    def test_cache_fingerprint_changes_with_name_progress_and_counter(self):
        report=synthetic_report(1)
        first=report_fingerprint(report)
        report['name']='新名字';self.assertNotEqual(first,report_fingerprint(report))
        second=report_fingerprint(report);report['rounds'][0]['data']['kills']+=1
        self.assertNotEqual(second,report_fingerprint(report))
        self.assertEqual(report_fingerprint(report),report_fingerprint(json.dumps(report).encode()))

    def test_actual_cjk_font_masks_are_not_missing_glyph_boxes(self):
        for title in (False,True):
            font=_font(32,title)
            self.assertTrue(font_path(title).is_file())
            missing=bytes(font.getmask('\U0010ffff'))
            for char in '墨境御剑山河鼓动彩水果武对决访客战绩麒麟龘':
                with self.subTest(title=title,char=char):
                    self.assertTrue(font.getmask(char).getbbox())
                    self.assertNotEqual(bytes(font.getmask(char)),missing)

    def test_real_store_json_report_is_supported_including_interrupted_round(self):
        with tempfile.TemporaryDirectory() as directory:
            store=VisitorStore(directory);current=store.current()
            store.rename(current['token'],'测试游客')
            for game,status in (('sword','completed'),('fight','interrupted'),('taiko','active')):
                store.record(current['id'],uuid.uuid4().hex,game,dict(score=99,accuracy=91.3,rank='A'),status)
            payload=store.report(current['token'],'json')
            self.assertEqual(report_manifest(payload)['round_count'],3)
            with Image.open(io.BytesIO(render_report_page(payload))) as image:
                self.assertEqual(json.loads(image.info['InkSuiteReport'])['round_count'],3)

    def test_invalid_page_sizes_and_pages_are_rejected(self):
        report=synthetic_report(1)
        for size in (0,21,True,2.5):
            with self.assertRaises(ValueError):report_manifest(report,size)
        for page in (0,-1,True,1.5,3):
            with self.assertRaises(ValueError):render_report_page(report,page)


if __name__=='__main__':
    unittest.main()
