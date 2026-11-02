import json
from pathlib import Path
import numpy as np
import pytest
from phases import body_phases

def elbow_at(shoulder,wrist,length=.18):
    delta=wrist-shoulder; distance=np.linalg.norm(delta); midpoint=(wrist+shoulder)/2
    perp=np.array([-delta[1],delta[0]])/max(distance,1e-6)
    elbow=midpoint+perp*np.sqrt(max(0,length**2-(distance/2)**2))
    # Use the lower intersection for the bent arm.
    other=midpoint-perp*np.sqrt(max(0,length**2-(distance/2)**2))
    return elbow if elbow[1]>other[1] else other

def two_stage_v():
    times=np.arange(0,18,1/30);points=np.zeros((len(times),33,4));points[:,:,3]=1
    for i,t in enumerate(times):
        shleft=np.array([.6,.3]);shright=np.array([.4,.3])
        points[i,11,:2]=shleft;points[i,12,:2]=shright
        points[i,23,:2]=[.55,.6];points[i,24,:2]=[.45,.6]
        if t<2: dx,dy=.02,.32
        elif t<3:
            u=t-2;dx,dy=.02*(1-u)+.01*u,.32*(1-u)+.015*u
        elif t<8: dx,dy=.01,.015
        elif t<8.5:
            u=(t-8)/.5;dx,dy=.01+.09*u,.015-.215*u
        elif t<9: dx,dy=.1,-.2
        elif t<11:
            u=(t-9)/2;dx,dy=.1+.195*u,-.2
        elif t<14: dx,dy=.295,-.2
        elif t<16:
            u=(t-14)/2;dx,dy=.295*(1-u)+.02*u,-.2*(1-u)+.32*u
        else: dx,dy=.02,.32
        left=shleft+[dx,dy];right=shright+[-dx,dy]
        points[i,15,:2]=left;points[i,16,:2]=right
        points[i,13,:2]=elbow_at(shleft,left);points[i,14,:2]=elbow_at(shright,right)
    return times,points

@pytest.mark.parametrize("speed",["SF","FS","MID"])
def test_height_plateau_does_not_end_v_before_elbows_extend(speed):
    t,p=two_stage_v()
    labels,d,w=body_phases(p,t,18,{"action_id":"A03","speed_instruction":speed},1000,1000)
    assert d["execute_end"]>=10.85 and d["execute_end"]<=11.3,d
    assert labels[np.argmin(abs(t-9.8))]=="execute"
    assert labels[np.argmin(abs(t-12))]=="hold"
    assert labels[np.argmin(abs(t-15))]=="return"

def test_clip_starting_mid_preparation_not_all_ready():
    t,p=two_stage_v();mask=t>=2.3;t=t[mask]-t[mask][0];p=p[mask]
    labels,d,w=body_phases(p,t,float(t[-1]+1/30),{"action_id":"A03"},1000,1000)
    assert labels[0]=="prepare"
    assert labels[np.argmin(abs(t-3))]=="ready"

@pytest.mark.parametrize("name,low,high",[("A03_SF",7.73,8.13),("A03_FS",7.43,7.90)])
def test_user_reported_regression(name,low,high):
    root=Path(__file__).parent/"regression_fixtures"
    with np.load(root/(name+".npz")) as z: p=z["points"];t=z["times"]
    metadata=json.loads((root/(name+".json")).read_text(encoding="utf-8"))
    labels,d,w=body_phases(p,t,metadata["duration"],metadata["metadata"],1920,1080)
    json.dumps(d,allow_nan=False)
    assert low<=d["execute_end"]<=high,d
    returns=[float(t[i]) for i in range(len(t)) if labels[i]=="return"]
    assert not returns or min(returns)>10.5
