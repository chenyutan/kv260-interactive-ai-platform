import argparse,json
from pathlib import Path
import numpy as np
from action_engine import ActionEngine

p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--replay',action='store_true');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
data=np.load(args.dataset/'templates.npz')
cal=json.loads((args.dataset/'speed_calibration.json').read_text())
config={'enabled_actions':['A01','A02','A03','A04','A06'],'min_score':.3,'min_distal_score':.2}
engine=ActionEngine(data,cal,config=config);assert engine._start_native is not None
rng=np.random.default_rng(35030);checks=0
poses=[t.start+rng.normal(0,s,(8,2)) for t in engine.templates for s in [0,.05,.25,.5]]
poses.extend(rng.normal(size=(200,8,2)))
def same(pose):
    global checks
    a=[id(t) for t in engine._start_matches(pose)]
    b=[id(t) for t in engine._start_matches_numpy(pose)]
    assert a==b;checks+=1
for pose in poses:same(pose)
for t in engine.templates:
    pose=t.start+rng.normal(0,.2,(8,2));threshold=t.pose_error(pose,t.start)
    for v in [threshold,np.nextafter(threshold,-np.inf),np.nextafter(threshold,np.inf)]:
        engine.config['start_distance']=float(v);same(pose)

class Original(ActionEngine):
    def _start_matches(self,pose):return self._start_matches_numpy(pose)
    def _stable(self,seconds,radius=None):
        if len(self.window)<4 or self.window[-1][0]-self.window[0][0]<seconds-1e-6:return False
        poses=np.stack([p for _,p in self.window]);center=np.median(poses,axis=0)
        value=float(np.percentile(np.sqrt(np.mean((poses-center)**2,axis=(1,2))),90))
        return value<=(self.config['stable_radius'] if radius is None else radius)

frames=0;videos=[]
if args.replay:
    for path in sorted((args.dataset/'poses').glob('*.npz')):
        if not path.name.startswith(('P001_','P003_')):continue
        cache=np.load(path)
        if 'points17' not in cache:continue
        before=Original(data,cal,config=config);after=ActionEngine(data,cal,config=config)
        for points,timestamp in zip(cache['points17'],cache['times']):
            a=before.update(points,float(timestamp),int(cache['width']),int(cache['height']))
            b=after.update(points,float(timestamp),int(cache['width']),int(cache['height']))
            assert a==b,(path.name,float(timestamp),a,b)
            frames+=1
        videos.append(path.name)
result={'native_loaded':True,'start_and_boundary_checks':checks,'replay_frames':frames,'replay_videos':videos,'all_decisions_equal':True}
args.output.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='replay_videos'}))
