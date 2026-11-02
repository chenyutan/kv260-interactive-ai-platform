import numpy as np
from control_phases import control_phases

def clip():
    t=np.arange(360)/30;p=np.zeros((len(t),33,4));p[:,:,:2]=.5;p[:,:,3]=1
    p[:,11,:2]=[.4,.4];p[:,12,:2]=[.6,.4]
    p[:,23,:2]=[.4,.7];p[:,24,:2]=[.6,.7]
    p[:,15,:2]=[.35,.8];p[:,16,:2]=[.65,.8]
    for i,x in enumerate(t):
        if 1<=x<2:p[i,16,:2]=np.array([.65,.8])+(x-1)*np.array([.15,-.4])
        elif 2<=x<4:p[i,16,:2]=[.8,.4]
        elif 4<=x<6 or 7<=x<9:
            angle=(x-(4 if x<6 else 7))*np.pi
            p[i,16,:2]=[.65+.15*np.cos(angle),.4+.15*np.sin(angle)]
        elif 6<=x<10:p[i,16,:2]=[.8,.4]
        elif 10<=x<11:p[i,16,:2]=np.array([.8,.4])+(x-10)*np.array([-.15,.4])
    return p,t

def test_turning_pause_is_continuous_but_preparation_is_separate():
    p,t=clip();labels,d,_=control_phases(p,t,12,{},640,480)
    assert labels[45]=='prepare' and labels[90]=='ready'
    assert labels[135:260]==['execute']*125
    assert labels[285]=='hold' and labels[315]=='return'
    assert d['events']==[] and d['active_wrist']==16

def test_control_missing_track_is_not_filled():
    p,t=clip();p[165:180,16,3]=0
    labels,_,_=control_phases(p,t,12,{},640,480)
    assert labels[165:180]==['unknown']*15

def test_control_filename_cannot_supply_motion_or_success():
    p,t=clip()
    a=control_phases(p,t,12,{'action_id':'G01','control_direction':'CW','control_validity':'POS'},640,480)
    b=control_phases(p,t,12,{'action_id':'G02','control_direction':'CCW','control_validity':'NEG'},640,480)
    assert a==b
    p[:]=p[0]
    labels,d,_=control_phases(p,t,12,{'control_validity':'POS'},640,480)
    assert set(labels)=={'unknown'} and d['events']==[]

def test_short_sweep_is_not_replaced_by_larger_lowering_motion():
    p,t=clip()
    for i,x in enumerate(t):
        if 4<=x<6:p[i,16,:2]=[.8-.2*(x-4)/2,.4]
        elif 6<=x<10:p[i,16,:2]=[.6,.4]
        elif 10<=x<11:p[i,16,:2]=[.6+.05*(x-10),.4+.4*(x-10)]
    labels,_,_=control_phases(p,t,12,{},640,480)
    assert labels[150]=='execute' and labels[315]=='return'

def test_free_activity_protocol_has_no_invented_control_phases():
    p,t=clip()
    labels,d,_=control_phases(p,t,12,{'negative_type':'BODYFREE','control_validity':'NEG'},640,480)
    assert set(labels)=={'negative'} and not d['events']
    assert d['label_source']=='filename_unverified' and not d['verified']
