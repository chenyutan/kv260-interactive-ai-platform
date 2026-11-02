"""Local recorded-video regressions; these are development examples, not a holdout."""
import json
from pathlib import Path
import numpy as np
import pytest
from core import resegment

def test_recorded_b06_slow_keeps_writing_hand_identity():
    from hand_phases import select_writing_hand
    path=Path(__file__).parent/'diagnostics/P003_B06_SLOW_R01_audit163.npz'
    if not path.exists():pytest.skip('Local diagnostic recording not installed')
    arrays=dict(np.load(path))
    points,_=select_writing_hand(arrays['hand_candidates'],1920,1080,{})
    # Visible raised writing hand, not the idle hand around y=.7.
    for time in (4.,6.,8.,10.,12.):
        i=np.argmin(abs(arrays['times']-time))
        assert points[i,0,3]>.5 and points[i,0,1]<.6

def test_long_state_keeps_confident_forward_identity():
    from hand_phases import select_writing_hand
    path=Path(__file__).parent/'diagnostics/P003_STATE_R01_audit163.npz'
    if not path.exists():pytest.skip('Local diagnostic recording not installed')
    arrays=dict(np.load(path))
    points,detail=select_writing_hand(arrays['hand_candidates'],1920,1080,{})
    assert detail['method']=='spatial_tracks_then_whole_clip_wrist_excursion'
    assert np.array_equal(points,arrays['hand_pre_refinement_points'])

def test_b06_static_closure_is_explicit_weak_inference():
    folder=Path(__file__).parent/'diagnostics';stem='P003_B06_FAST_R01_audit163'
    if not (folder/f'{stem}.npz').exists():pytest.skip('Local diagnostic recording not installed')
    data=json.loads((folder/f'{stem}.json').read_text(encoding='utf-8'))
    arrays=dict(np.load(folder/f'{stem}.npz'));original=arrays['points'].copy()
    result,_=resegment(data,arrays)
    assert [s['phase'] for s in result['segments']]==['up','down','up']
    assert 3.5<result['segments'][1]['start']<3.8
    assert 9.2<result['segments'][1]['end']<9.5
    intervals=result['segmentation']['inferred_state_intervals']
    assert any(x['evidence']=='sustained_closed_shape_followed_by_confirmed_pinch' and not x['verified'] for x in intervals)
    assert np.array_equal(original,arrays['points'])

def test_state_projected_overlap_is_not_pinch_and_true_pinches_remain():
    folder=Path(__file__).parent/'diagnostics';stem='P003_STATE_R01_audit163'
    if not (folder/f'{stem}.npz').exists():pytest.skip('Local diagnostic recording not installed')
    data=json.loads((folder/f'{stem}.json').read_text(encoding='utf-8'))
    result,_=resegment(data,dict(np.load(folder/f'{stem}.npz')))
    for time,expected in [(23.5,'up'),(35.,'down'),(48.,'down'),(51.,'up'),(53.,'down'),(56.6,'up'),(88.,'down'),(91.,'up')]:
        assert next(s['phase'] for s in result['segments'] if s['start']<=time<s['end'])==expected

def test_b06_mid_real_out_of_frame_gap_is_not_filled():
    folder=Path(__file__).parent/'diagnostics';stem='P003_B06_MID_R01_audit163'
    if not (folder/f'{stem}.npz').exists():pytest.skip('Local diagnostic recording not installed')
    data=json.loads((folder/f'{stem}.json').read_text(encoding='utf-8'))
    result,_=resegment(data,dict(np.load(folder/f'{stem}.npz')))
    assert any(s['phase']=='unknown' and s['start']<11.1<s['end'] for s in result['segments'])

@pytest.mark.parametrize('stem,start,end',[
    ('B04_FAST_roi_trial',18.7,23.67),
    ('B04_MID_full_160',1.6,8.87),
    ('B04_SLOW_roi_trial',12.3,16.3)])
def test_recorded_pinch_hold_and_release(stem,start,end):
    folder=Path(__file__).parent/'diagnostics'
    if not (folder/f'{stem}.npz').exists():pytest.skip('Local diagnostic recording not installed')
    data=json.loads((folder/f'{stem}.json').read_text(encoding='utf-8'))
    arrays=dict(np.load(folder/f'{stem}.npz'))
    result,_=resegment(data,arrays)
    assert [s['phase'] for s in result['segments']]==['up','down','up']
    down=result['segments'][1]
    assert abs(down['start']-start)<.1 and abs(down['end']-end)<.1

def test_real_out_of_frame_gap_is_preserved():
    folder=Path(__file__).parent/'diagnostics';stem='P003_B02_FAST_1.5.1'
    if not (folder/f'{stem}.npz').exists():pytest.skip('Local diagnostic recording not installed')
    data=json.loads((folder/f'{stem}.json').read_text(encoding='utf-8'))
    result,_=resegment(data,dict(np.load(folder/f'{stem}.npz')))
    assert any(s['phase']=='unknown' and s['start']<12.2<s['end'] for s in result['segments'])

def test_recorded_sleeve_weak_release_is_inferred_not_hidden():
    folder=Path(__file__).parent/'diagnostics';stem='B05_FAST_161_trial'
    if not (folder/f'{stem}.npz').exists():pytest.skip('Local diagnostic recording not installed')
    data=json.loads((folder/f'{stem}.json').read_text(encoding='utf-8'))
    arrays=dict(np.load(folder/f'{stem}.npz'));original=arrays['points'].copy()
    result,_=resegment(data,arrays)
    assert [s['phase'] for s in result['segments']]==['up','down','up']
    assert abs(result['segments'][1]['start']-7.1)<.05
    assert abs(result['segments'][1]['end']-13.967)<.05
    inferred=result['segmentation']['inferred_state_intervals']
    assert len(inferred)==2 and all(not x['verified'] and not x['coordinates_interpolated'] for x in inferred)
    assert np.array_equal(original,arrays['points'])
