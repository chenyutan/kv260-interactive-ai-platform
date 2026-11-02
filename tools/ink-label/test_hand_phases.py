import json
import numpy as np
from hand_phases import hand_phases,hand_features
from core import propose,resegment,queue_videos,blank,store_analysis
from test_inklabel import video

def test_weak_release_requires_both_anchors_and_no_strong_open_or_loss():
    from hand_phases import recover_ambiguous_release
    t=np.arange(90)/30;labels=['down']*90;labels[30:45]=['up']*15
    f=dict(valid=np.ones(90,bool),pinch_ratio=np.full(90,.75),
           finger_angles=np.full((90,5),150.),fingertip_spread=np.full(90,.5),
           palm_foreshortened=np.ones(90,bool))
    recovered,intervals=recover_ambiguous_release(labels,f,t)
    assert recovered==['down']*90 and len(intervals)==1
    f['pinch_ratio'][35]=1.3
    assert recover_ambiguous_release(labels,f,t)[0]==labels
    f['pinch_ratio'][35]=.9;f['finger_angles'][35,1:]=178
    assert recover_ambiguous_release(labels,f,t)[0]==labels
    f['pinch_ratio'][35]=.75;f['finger_angles'][35,1:]=150;f['valid'][35]=False
    assert recover_ambiguous_release(labels,f,t)[0]==labels
    f['valid'][35]=True;labels[45:]=['up']*45
    assert recover_ambiguous_release(labels,f,t)[0]==labels

def test_dorsal_closure_needs_visible_pinch_and_preserves_release():
    from hand_phases import recover_hidden_pinch_onset
    t=np.arange(180)/30
    angles=np.full((180,5),175.)
    angles[30:120]=[140,130,120,120,120]
    f={'finger_angles':angles,'pinch_ratio':np.full(180,.55),'valid':np.ones(180,bool)}
    labels=['up']*180;labels[120:150]=['down']*30
    recovered,intervals=recover_hidden_pinch_onset(labels,f,t)
    assert recovered[29]=='up' and recovered[30:150]==['down']*120
    assert recovered[150:]==['up']*30
    assert intervals[0]['source']=='hand_shape_inference'
    # A closed fist without subsequent visible contact is not a pinch.
    assert recover_hidden_pinch_onset(['up']*180,f,t)[1]==[]
    # Missing observations and opening before pinching block backfill.
    f['valid'][119]=False
    assert recover_hidden_pinch_onset(labels,f,t)[1]==[]
    f['valid'][119]=True;angles[115:120]=175
    assert recover_hidden_pinch_onset(labels,f,t)[1]==[]

def test_dorsal_inference_does_not_use_motion_or_speed_class():
    p,t=sample()
    expected=hand_phases(p,t,5,{'speed_instruction':'FAST'},640,480)[0]
    for speed in ['MID','SLOW','SF','FS']:
        translated=p.copy();translated[:,:,0]+=np.linspace(0,.1,len(t))[:,None]
        assert hand_phases(translated,t,5,{'speed_instruction':speed},640,480)[0]==expected

def test_rotated_crop_coordinates_roundtrip():
    from hand_recovery import unrotate_xy
    original=(.2,.7)
    rotated=[(.2,.7),(.7,.8),(.8,.3),(.3,.2)]
    for turn,(x,y) in enumerate(rotated):assert np.allclose(unrotate_xy(x,y,turn),original)

def test_side_projection_retains_pinch_until_clear_release():
    p,t=sample()
    p[60:,6,1]=.35;p[60:,7,1]=.25
    p[60:120,4,0]=.46;p[60:120,8,0]=.54
    p[120:,4,0]=.40;p[120:,8,0]=.60
    labels,_,_=hand_phases(p,t,5,{},640,480)
    assert labels[30:120]==['down']*90
    assert labels[120:]==['up']*30

def test_track_recovery_does_not_open_video_without_gaps():
    from hand_recovery import recover_track_gaps
    p,t=sample();result,mask=recover_track_gaps('nonexistent.mp4',{'points':p,'times':t},640,480,None)
    assert np.array_equal(result,p) and not mask.any()

def test_dorsal_recovery_never_invents_an_unanchored_hand():
    from hand_recovery import recover_with_pose
    p=np.zeros((3,21,4));a={'points':p,'times':np.arange(3)/30}
    result,mask=recover_with_pose('nonexistent.mp4',a,640,480,None,'missing.task')
    assert np.array_equal(result,p) and not mask.any()

def test_select_active_hand_and_never_substitute_other():
    from hand_phases import select_writing_hand
    p,t=sample();c=np.stack([p,p],axis=1)
    c[:,0,:,0]-=.3
    c[:,1,:,0]+=np.linspace(0,.3,len(t))[:,None]
    c[60:70,1,:,3]=0
    selected,detail=select_writing_hand(c,640,480,{})
    assert detail['model_side']=='Right'
    assert (selected[60:70,:,3]==0).all()

def test_handedness_flicker_does_not_break_tracking():
    from hand_phases import select_writing_hand
    p,t=sample();c=np.stack([p,p],axis=1);c[:,0,:,0]-=.3
    c[:,1,:,0]+=np.linspace(0,.3,len(t))[:,None]
    expected=c[:,1].copy();c[50:80]=c[50:80,::-1].copy()
    selected,_=select_writing_hand(c,640,480,{})
    assert np.allclose(selected,expected)

def test_sparse_duplicate_cannot_steal_continuous_hand():
    from hand_phases import select_writing_hand
    p,t=sample();p[:,:,0]+=np.linspace(0,.25,len(t))[:,None]
    c=np.zeros((len(t),2,21,4));c[:,1]=p
    c[32,0]=p[32];c[32,0,:,0]+=.01
    c[90,0]=p[90];c[90,0,:,0]-=.01
    selected,_=select_writing_hand(c,640,480,{})
    assert np.all(selected[:,0,3]>.5)
    assert np.allclose(selected,p)

def sample():
    t=np.arange(150)/30
    p=np.zeros((len(t),21,4));p[:,:,0]=.5;p[:,:,1]=.5;p[:,:,3]=1
    p[:,0,1]=.7;p[:,9,1]=.5;p[:,5,0]=.45;p[:,17,0]=.55
    p[:,4,0]=.46;p[:,8,0]=.54
    p[30:120,4,0]=.49;p[30:120,8,0]=.51
    return p,t

def test_hand_state_boundaries_and_features():
    p,t=sample();labels,d,_=hand_phases(p,t,5,{'action_id':'B02'},640,480)
    assert labels[0]=='up' and labels[30]=='down' and labels[120]=='up'
    assert d['stroke_count']==1 and d['strokes'][0]['start']==1
    assert [e['type'] for e in d['events']]==['pen_down','pen_up']
    assert hand_features(p,t,640,480)['finger_angles'].shape==(150,5)
    json.dumps(d,allow_nan=False)

def test_pause_and_sf_do_not_split_stroke():
    p,t=sample();p[40:55,:,0]+=np.linspace(0,.15,15)[:,None];p[55:90,:,0]+=.15
    for speed in ['SF','FS','SLOW']:
        _,d,_=hand_phases(p,t,5,{'speed_instruction':speed},640,480)
        assert d['stroke_count']==1

def test_occlusion_breaks_ink_and_unknown_reacquisition():
    p,t=sample();p[65:70,:,3]=0
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65:70]==['unknown']*5 and d['stroke_count']==2
    assert d['strokes'][0]['incomplete_end'] and d['strokes'][1]['incomplete_start']
    _,v,_,_=propose(p,t,5,{'category':'HAND'},640,480,True)
    assert np.isnan(v[65:71]).all()

def test_one_frame_pinch_is_not_a_stroke():
    p,t=sample();p[:,:,3]=1;p[:,4,0]=.46;p[:,8,0]=.54
    p[50,4,0]=.49;p[50,8,0]=.51
    _,d,_=hand_phases(p,t,5,{},640,480)
    assert d['stroke_count']==0

def test_half_pinch_at_start_is_unknown():
    p,t=sample();p[:,4,0]=.472;p[:,8,0]=.528
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert set(labels)=={'unknown'} and d['stroke_count']==0

def test_large_translation_preserves_visible_pinch_and_speed():
    p,t=sample()
    # A coherent, fast hand translation exceeds the old 2.5-palm gate.
    p[60:,:,0]+=.4
    f=hand_features(p,t,640,480)
    assert f['motion_jump'][60] and f['valid'][60]
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[60]=='down' and d['stroke_count']==1
    _,speed,_,_=propose(p,t,5,{'category':'HAND'},640,480,True)
    assert np.isfinite(speed[60])

def test_reacquired_hand_has_no_extra_jump_rejection():
    p,t=sample();p[65,:,3]=0
    f=hand_features(p,t,640,480)
    assert not f['valid'][65] and f['valid'][66]
    labels,_,_=hand_phases(p,t,5,{},640,480)
    assert labels[65]=='down' and labels[66]=='down'
    _,speed,_,_=propose(p,t,5,{'category':'HAND'},640,480,True)
    assert np.isnan(speed[65:67]).all() and np.isfinite(speed[67])

def test_short_gap_state_recovery_is_recorded_without_fake_coordinates():
    p,t=sample();p[65:67,:,3]=0;original=p.copy()
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert d['stroke_count']==1 and labels[65:67]==['down','down']
    assert len(d['inferred_state_intervals'])==1
    assert not d['inferred_state_intervals'][0]['coordinates_interpolated']
    assert np.array_equal(original,p)

def test_fast_travel_across_four_missing_frames_keeps_pen_state():
    p,t=sample();p[65:69,:,3]=0;p[69:,:,0]+=.4
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65:69]==['down']*4 and d['stroke_count']==1
    _,speed,_,_=propose(p,t,5,{'category':'HAND'},640,480,True)
    assert np.isnan(speed[65:70]).all()

def test_side_palm_short_gap_recovers_but_long_gap_stays_unknown():
    p,t=sample();p[50:90,9,1]=.69;p[65:70,:,3]=0
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65:70]==['down']*5 and d['stroke_count']==1
    p[65:74,:,3]=0
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65:74]==['unknown']*9

def test_short_real_release_is_not_bridged():
    p,t=sample();p[65:68,4,0]=.46;p[65:68,8,0]=.54
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65:68]==['up']*3 and d['stroke_count']==2
    assert not d['inferred_state_intervals']

def test_turning_palm_does_not_imply_pen_release():
    p,t=sample()
    # Folded wrist-to-middle projection is 4.8px, while MCP width remains 64px.
    p[60:70,9,1]=.69
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[60:70]==['down']*10 and d['stroke_count']==1

def test_short_gap_then_ambiguous_pinch_retains_state():
    p,t=sample();p[65,:,3]=0;p[66,4,0]=.472;p[66,8,0]=.528
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[64:70]==['down']*6 and d['stroke_count']==1

def test_brief_weak_release_only_filtered_when_foreshortened():
    p,t=sample();p[65:68,9,1]=.69;p[65:68,4,0]=.473;p[65:68,8,0]=.527
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65:68]==['down']*3 and d['stroke_count']==1

def test_clearly_open_foreshortened_hand_still_releases():
    p,t=sample();p[65:75,9,1]=.69;p[65:75,4,0]=.42;p[65:75,8,0]=.58
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65:75]==['up']*10 and d['stroke_count']==2

def test_half_pinch_and_different_states_not_bridged():
    p,t=sample();p[65:70,:,3]=0;p[70:,4,0]=.46;p[70:,8,0]=.54
    labels,d,_=hand_phases(p,t,5,{},640,480)
    assert labels[65]=='unknown' and not d['inferred_state_intervals']

def test_hand_resegment_preserves_manual_events(tmp_path):
    v=tmp_path/'P001_S01_C01_HAND_B02_MID_R01.mp4';v.touch()
    d=blank(v);d['duration']=5;d['events']=[dict(time=.5,type='confirm',source='human')]
    d['analysis']=dict(width=640,height=480,kind='hand')
    p,t=sample();a=dict(points=p,times=t,frame_times=t,speed=np.zeros(len(t)))
    store_analysis(tmp_path,d,a)
    result,_=resegment(d,a)
    assert result['events']==d['events'] and len(result['suggested_events'])==2
    assert queue_videos([str(v)],tmp_path,'phases')[0]==[str(v)]

def test_hand_gui_and_export_corrected_features(video,tmp_path):
    from PySide6.QtWidgets import QApplication
    from app import Window
    from core import export_reviewed,save_json,annotation_path
    p,t=sample();p=p[:45];t=t[:45]
    d=blank(video);d['metadata'].update(category='HAND',action_id='B02',filename_label_verified=True)
    d['duration']=1.5;d['analysis']=dict(kind='hand',width=320,height=240)
    arrays=dict(points=p,times=t,frame_times=t,speed=np.zeros(len(t)))
    d,_=resegment(d,arrays);d['status']='reviewed'
    d['corrections']={'35':{'4':[.48,.5,0,1]}}
    store_analysis(tmp_path,d,arrays)
    save_json(tmp_path/'project.json',dict(schema='inklabel.project.v1',videos=[str(video)]))
    application=QApplication.instance() or QApplication([])
    win=Window()
    try:
        win.open_project(tmp_path);win.show();application.processEvents()
        win.seek(1.1)
        assert not win.hand_caption.isHidden()
        assert win.suggested_event_list.count()==1
        assert win.preview.points.shape==(21,4)
    finally:
        win.close();application.processEvents()
    out=tmp_path/'export';assert export_reviewed(tmp_path,[str(video)],out)==(1,0)
    z=np.load(next(out.glob('*.npz')))
    assert z['points'][35,4,0]==.48
    assert 'hand_finger_angles' in z.files
    assert np.isnan(z['ink_speed'][:30]).all()
