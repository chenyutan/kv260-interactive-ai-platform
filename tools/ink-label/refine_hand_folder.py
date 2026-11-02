"""Finish a full rebuild with local RGB re-detection and final phase proposals."""
from core import *
from hand_recovery import recover_gaps

def main():
    import sys
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    root=Path(sys.argv[1]);model=Path(__file__).parent/'models'/'hand_landmarker.task'
    report=[]
    options=vision.HandLandmarkerOptions(base_options=python.BaseOptions(model_asset_buffer=model.read_bytes()),running_mode=vision.RunningMode.IMAGE,num_hands=2)
    with vision.HandLandmarker.create_from_options(options) as detector:
        for path in sorted((root/'annotations').glob('*.json')):
            d=json.loads(path.read_text(encoding='utf-8'))
            if d.get('analysis',{}).get('kind')!='hand':continue
            if d.get('analysis',{}).get('roi_recovery_version')=='1':continue
            with np.load(root/d['keypoints_file']) as z:a={k:z[k] for k in z.files}
            original=a['points'].copy()
            p,recovered=recover_gaps(d['video'],a,d['analysis']['width'],d['analysis']['height'],detector)
            for k in d.get('corrections',{}):recovered[int(k)]=False
            a['points'][recovered]=p[recovered]
            a['hand_roi_recovered']=recovered
            d['analysis']['roi_recovery_version']='1'
            d['analysis']['roi_recovered_samples']=int(recovered.sum())
            d,a['speed']=resegment(d,a);store_analysis(root,d,a)
            row=dict(video=Path(d['video']).name,recovered=int(recovered.sum()),strokes=d['segmentation']['stroke_count'],bridges=len(d['segmentation']['inferred_state_intervals']))
            report.append(row);print(row,flush=True)
    save_json(root/'hand_refinement_1.4_report.json',{'version':VERSION,'updated':report,'finished_at':now()})

if __name__=='__main__':main()
