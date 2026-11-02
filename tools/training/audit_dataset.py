"""Read-only inventory for training preparation; never rewrites InkLabel labels."""
import collections,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ink-label'))
from core import load_or_blank,annotation_path,validate

def audit(project):
    project=Path(project);records=[];errors=[];counts=collections.Counter()
    for video in json.loads((project/'project.json').read_text('utf8'))['videos']:
        try:
            d=load_or_blank(project,video);m=d['metadata'];a=d.get('analysis',{})
            intervals=d.get('segmentation',{}).get('inferred_state_intervals',[])
            row=dict(video=video,annotation=str(annotation_path(project,video)),
                annotation_sha256=hashlib.sha256(annotation_path(project,video).read_bytes()).hexdigest(),
                fingerprint=d['fingerprint'],person=m.get('person_id'),clothing=m.get('clothing_id'),
                category=m.get('category'),action=m.get('action_id'),status=d['status'],
                needs_reshoot=bool(m.get('needs_reshoot')),duration=d['duration'],
                keypoints_file=str(project/d['keypoints_file']) if d.get('keypoints_file') else None,
                landmarks_verified=bool(a.get('landmarks_verified')),inferred_intervals=intervals,
                unknown_intervals=[s for s in d['segments'] if s['phase']=='unknown'],
                validation_errors=validate(d,d['status']=='reviewed'),
                split='pending_grouped_split',identity_hold=m.get('person_id') not in ('P001','P003','P004'))
            # This inventory is NOT a ready-to-train ground-truth export.
            records.append(row);counts[f"{row['person']}/{row['clothing']}/{row['category']}/{row['status']}"]+=1
        except Exception as exc:errors.append(dict(video=video,error=str(exc)))
    return dict(project=str(project),purpose='inventory_only_not_training_ground_truth',
                counts=dict(counts),records=records,errors=errors,
                policy={'split_by':'person_and_original_recording','legacy_app_split_ignored':True,
                        'inferred_intervals':'exclude_from_hard_supervision_until_explicit_review',
                        'keypoints':'pseudo_labels_unless_independently_verified'})

if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('Usage: audit_dataset.py PRIVATE_PROJECT_DIRECTORY')
    project=sys.argv[1]
    output=Path(__file__).with_name('dataset_inventory.json');result=audit(project)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'videos':len(result['records']),'errors':len(result['errors']),
                     'verified_landmark_videos':sum(x['landmarks_verified'] for x in result['records']),
                     'videos_with_inference':sum(bool(x['inferred_intervals']) for x in result['records']),
                     'identity_hold':sum(x['identity_hold'] for x in result['records'])}))
