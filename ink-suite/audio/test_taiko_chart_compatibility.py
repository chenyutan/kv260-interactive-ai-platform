"""Semantic audio/chart regressions; no playback or asset mutation."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from .build_assets import ROOT, chart_accent_alignment, chart_compatibility, load_chart


class ChartAudioCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.path = load_chart()
        cls.manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
        cls.schedule = json.loads((ROOT / 'qa/taiko-accent-schedule.json').read_text(encoding='utf-8'))
        cls.baseline = json.loads((ROOT / 'qa/taiko-chart-baseline-20260929.json').read_text(encoding='utf-8'))

    def changed(self, edit, **constants):
        def chart(level):
            notes = copy.deepcopy(self.module.chart(level))
            edit(level, notes)
            return notes
        return SimpleNamespace(chart=chart, BPM=constants.get('BPM', self.module.BPM),
                               BEAT=constants.get('BEAT', self.module.BEAT),
                               DURATION=constants.get('DURATION', self.module.DURATION),
                               CLAP_TIMES=constants.get('CLAP_TIMES', self.module.CLAP_TIMES),
                               DIFFICULTIES=self.module.DIFFICULTIES)

    def inspect(self, module=None, baseline=None, schedule=None):
        return chart_compatibility(module or self.module, self.path, self.manifest,
                                   self.schedule if schedule is None else schedule,
                                   self.baseline if baseline is None else baseline)

    def test_new_source_and_hard_keep_original_waveform_provenance(self):
        protected = [ROOT / 'manifest.json', ROOT / 'qa/taiko-chart-baseline-20260929.json',
                     ROOT / 'qa/taiko-accent-schedule.json']
        # Build-only reports need not be installed with the board runtime.
        # Preserve their bytes whenever present, without fabricating a new
        # historical report merely to satisfy the deployed test environment.
        protected += [path for path in (ROOT / 'qa/legacy-audio-baseline-20260929.json',
                                      ROOT / 'qa/report.json') if path.is_file()]
        protected += [ROOT / entry['file'] for group in ('tracks', 'effects')
                      for entry in self.manifest[group].values()]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        report = self.inspect()
        self.assertTrue(report['passed'])
        self.assertTrue(report['source_changed'])
        self.assertTrue(report['archive_event_baseline_used'])
        self.assertNotEqual(report['current_chart_sha256'], report['build_time_chart_sha256'])
        self.assertEqual(report['build_time_chart_sha256'], self.baseline['source_chart_sha256'])
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}, before)
        self.assertEqual(report['legacy_levels']['easy']['note_count'], 57)
        self.assertEqual(report['legacy_levels']['normal']['note_count'], 69)
        self.assertTrue(report['hard']['passed'])
        self.assertTrue(report['clap_declaration_matches_authorization'])
        self.assertFalse(report['audio_manifest_mutated'])
        self.assertFalse(report['waveform_regenerated'])

    def test_authorized_remaps_are_explicit_and_keep_the_original_lane_accent(self):
        report = self.inspect()
        expected = {
            'easy': [(7, 10.8, 'R'), (11, 15.6, 'B'), (25, 32.4, 'B'), (49, 61.2, 'L')],
            'normal': [(7, 10.8, 'R'), (11, 15.6, 'B'), (49, 51.6, 'L'), (59, 61.2, 'L')],
            'hard': [(7, 10.8, 'R'), (57, 42., 'R'), (64, 50.4, 'B'), (85, 68.4, 'R')],
        }
        self.assertEqual(len(report['authorized_clap_remaps']), 12)
        for difficulty, entries in expected.items():
            remaps = [r for r in report['authorized_clap_remaps'] if r['difficulty'] == difficulty]
            self.assertEqual([(r['id'], r['time'], r['original_lane']) for r in remaps], entries)
            self.assertTrue(all(r['new_lane'] == 'C' for r in remaps))
            for remap in remaps:
                accent = remap['accent_source']
                self.assertEqual(accent['time'], remap['time'])
                self.assertEqual(accent['sample'], round(remap['time'] * 48000))
                if difficulty == 'hard' and remap['time'] == 42.:
                    self.assertEqual(accent['kind'], 'existing_100bpm_beat_accompaniment')
                    self.assertNotIn('label', accent)
                else:
                    self.assertEqual(accent['kind'], 'archived_chart_accent')
                    self.assertEqual(accent['label'], 'chart:' + remap['original_lane'])
        for level in report['legacy_levels'].values():
            # The immutable build-time table actually has drum lanes, not C.
            self.assertFalse(level['events_equal_to_build_time'])
            self.assertTrue(level['events_equal_after_authorized_clap_remaps'])
            self.assertTrue(level['authorized_claps_complete'])

    def test_hard_baseline_is_the_later_release_not_the_waveform_build_source(self):
        report = self.inspect()
        hard = report['hard']
        baseline = hard['release_event_baseline']
        self.assertEqual(hard['note_count'], 87)
        self.assertFalse(hard['build_time_hard_chart_present'])
        self.assertEqual(baseline['source_chart_sha256'],
                         '05263e7ed2cfc0f161a328244f84f1f6d85e4887e73930af97a2875416e800d6')
        self.assertNotEqual(baseline['source_chart_sha256'], report['build_time_chart_sha256'])
        self.assertEqual(baseline['events_sha256'],
                         '8196ab886ef089887a6f93fd89e73ab90cb7fa6d38f64fc453679e9169d54798')
        self.assertEqual(hard['normalized_events_sha256'], baseline['events_sha256'])
        self.assertTrue(hard['events_equal_to_release_baseline'])

    def test_moved_note_is_incompatible(self):
        def edit(level, notes):
            if level == 'easy':
                notes[0]['time'] += .1
        report = self.inspect(self.changed(edit))
        self.assertFalse(report['passed'])
        self.assertFalse(report['legacy_levels']['easy']['events_equal_to_build_time'])
        self.assertTrue(report['legacy_levels']['easy']['missing_accents'])

    def test_swap_to_an_existing_accent_still_fails_exact_level_comparison(self):
        def edit(level, notes):
            if level == 'easy':
                notes[0]['time'], notes[0]['lane'] = notes[1]['time'], notes[1]['lane']
        report = self.inspect(self.changed(edit))
        self.assertFalse(report['passed'])
        self.assertEqual(report['legacy_levels']['easy']['missing_accents'], [])
        self.assertFalse(report['legacy_levels']['easy']['events_equal_to_build_time'])

    def test_changed_source_cannot_be_accepted_without_archive_baseline(self):
        report = self.inspect(baseline={})
        self.assertFalse(report['passed'])
        self.assertFalse(report['archive_event_baseline_used'])

    def test_wrong_sample_index_is_incompatible(self):
        schedule = copy.deepcopy(self.schedule)
        next(e for e in schedule if e['label'] == 'chart:L')['sample'] += 48
        self.assertFalse(self.inspect(schedule=schedule)['passed'])

    def test_new_hard_note_between_existing_beats_is_rejected(self):
        def edit(level, notes):
            if level == 'hard':
                notes[0]['time'] += .3
        report = self.inspect(self.changed(edit))
        self.assertFalse(report['passed'])
        self.assertTrue(report['hard']['missing_background_beats'])

    def test_hard_drum_lane_change_on_the_same_valid_beat_is_rejected(self):
        def edit(level, notes):
            if level == 'hard':
                notes[0]['lane'] = 'R'
        report = self.inspect(self.changed(edit))
        self.assertFalse(report['passed'])
        self.assertFalse(report['hard']['events_equal_to_release_baseline'])
        self.assertEqual(report['hard']['missing_background_beats'], [])

    def test_undeclared_clap_at_an_existing_beat_is_rejected_in_every_level(self):
        for difficulty in ('easy', 'normal', 'hard'):
            with self.subTest(difficulty=difficulty):
                def edit(level, notes):
                    if level == difficulty:
                        notes[0]['lane'] = 'C'
                report = self.inspect(self.changed(edit))
                self.assertFalse(report['passed'])
                details = report['hard'] if difficulty == 'hard' else report['legacy_levels'][difficulty]
                self.assertEqual(details['unauthorized_clap_notes'], [dict(id=0, time=2.4, lane='C')])

    def test_caller_cannot_authorize_a_new_clap_by_expanding_its_declaration(self):
        declarations = copy.deepcopy(self.module.CLAP_TIMES)
        declarations['hard'] = (2.4,) + declarations['hard']
        def edit(level, notes):
            if level == 'hard':
                notes[0]['lane'] = 'C'
        report = self.inspect(self.changed(edit, CLAP_TIMES=declarations))
        self.assertFalse(report['clap_declaration_matches_authorization'])
        self.assertFalse(report['passed'])
        self.assertTrue(report['hard']['unauthorized_clap_notes'])

    def test_missing_or_replaced_clap_declaration_is_not_a_new_authorization(self):
        for declaration in (None, {}, dict(self.module.CLAP_TIMES, easy=(12., 15.6, 32.4, 61.2))):
            with self.subTest(declaration=declaration):
                report = self.inspect(self.changed(lambda *_: None, CLAP_TIMES=declaration))
                self.assertFalse(report['clap_declaration_matches_authorization'])
                self.assertFalse(report['passed'])

    def test_canceling_an_authorized_clap_is_rejected_even_if_original_table_matches(self):
        originals = {'easy': 'R', 'normal': 'R', 'hard': 'R'}
        for difficulty, original in originals.items():
            with self.subTest(difficulty=difficulty):
                def edit(level, notes):
                    if level == difficulty:
                        notes[7]['lane'] = original
                report = self.inspect(self.changed(edit))
                self.assertFalse(report['passed'])
                details = report['hard'] if difficulty == 'hard' else report['legacy_levels'][difficulty]
                self.assertFalse(details['authorized_claps_complete'])

    def test_swapping_clap_to_a_different_existing_note_is_rejected(self):
        for difficulty in ('easy', 'normal', 'hard'):
            with self.subTest(difficulty=difficulty):
                def edit(level, notes):
                    if level == difficulty:
                        notes[7]['lane'] = 'R'
                        notes[0]['lane'] = 'C'
                report = self.inspect(self.changed(edit))
                self.assertFalse(report['passed'])
                details = report['hard'] if difficulty == 'hard' else report['legacy_levels'][difficulty]
                self.assertFalse(details['authorized_claps_complete'])
                self.assertTrue(details['unauthorized_clap_notes'])

    def test_authorized_clap_must_keep_its_original_id_and_time(self):
        for field, value in (('id', 700), ('time', 12.)):
            for difficulty in ('easy', 'normal', 'hard'):
                with self.subTest(field=field, difficulty=difficulty):
                    def edit(level, notes):
                        if level == difficulty:
                            notes[7][field] = value
                    report = self.inspect(self.changed(edit))
                    self.assertFalse(report['passed'])
                    details = report['hard'] if difficulty == 'hard' else report['legacy_levels'][difficulty]
                    self.assertTrue(details['unauthorized_clap_notes'])

    def test_insert_and_delete_notes_are_rejected_even_on_valid_background_beats(self):
        for action in ('insert', 'delete'):
            for difficulty in ('easy', 'normal', 'hard'):
                with self.subTest(action=action, difficulty=difficulty):
                    def edit(level, notes):
                        if level == difficulty:
                            if action == 'delete':
                                notes.pop(0)
                            else:
                                notes.insert(1, dict(id=999, time=3., lane='R'))
                    self.assertFalse(self.inspect(self.changed(edit))['passed'])

    def test_hard_note_move_to_another_existing_beat_is_still_rejected(self):
        def edit(level, notes):
            if level == 'hard':
                notes[0]['time'] = 3.
        report = self.inspect(self.changed(edit))
        self.assertEqual(report['hard']['missing_background_beats'], [])
        self.assertFalse(report['hard']['events_equal_to_release_baseline'])
        self.assertFalse(report['passed'])

    def test_remapped_original_accent_sample_and_presence_remain_required(self):
        for action in ('misalign', 'remove'):
            with self.subTest(action=action):
                schedule = copy.deepcopy(self.schedule)
                accent = next(e for e in schedule if e['time'] == 10.8 and e['label'] == 'chart:R')
                if action == 'misalign':
                    accent['sample'] += 48
                else:
                    schedule.remove(accent)
                report = self.inspect(schedule=schedule)
                self.assertFalse(report['passed'])
                self.assertFalse(report['legacy_levels']['easy']['passed'])
                self.assertFalse(report['legacy_levels']['normal']['passed'])

    def test_pcm_alignment_uses_explicit_authorized_remaps_without_hiding_rejections(self):
        report = self.inspect()
        alignment = chart_accent_alignment(report)
        for difficulty, count in (('easy', 57), ('normal', 69)):
            self.assertTrue(alignment[difficulty]['passed'])
            self.assertEqual(alignment[difficulty]['note_count'], count)
            self.assertEqual(alignment[difficulty]['missing_accents'], [])
            self.assertEqual(len(alignment[difficulty]['authorized_clap_remaps']), 4)
        def unauthorized(level, notes):
            if level == 'easy':
                notes[0]['lane'] = 'C'
        rejected = chart_accent_alignment(self.inspect(self.changed(unauthorized)))
        self.assertFalse(rejected['easy']['passed'])
        self.assertIn((2.4, 'C'), rejected['easy']['missing_accents'])

    def test_every_other_single_lane_edit_is_rejected(self):
        for difficulty in ('easy', 'normal', 'hard'):
            for index, note in enumerate(self.module.chart(difficulty)):
                for replacement in ('L', 'R', 'B', 'C'):
                    if replacement == note['lane']:
                        continue
                    with self.subTest(difficulty=difficulty, id=note['id'], replacement=replacement):
                        def edit(level, notes):
                            if level == difficulty:
                                notes[index]['lane'] = replacement
                        self.assertFalse(self.inspect(self.changed(edit))['passed'])

    def test_removing_the_existing_hard_level_is_rejected(self):
        module = self.changed(lambda *_: None)
        module.DIFFICULTIES = {k: v for k, v in module.DIFFICULTIES.items() if k != 'hard'}
        report = self.inspect(module)
        self.assertFalse(report['hard']['present'])
        self.assertFalse(report['hard']['passed'])
        self.assertFalse(report['passed'])

    def test_tempo_and_duration_changes_are_rejected(self):
        for constants in (dict(BPM=120), dict(BEAT=.5), dict(DURATION=70.)):
            report = self.inspect(self.changed(lambda *_: None, **constants))
            self.assertFalse(report['passed'], constants)
            self.assertFalse(report['timeline_compatible'], constants)


if __name__ == '__main__':
    unittest.main()
