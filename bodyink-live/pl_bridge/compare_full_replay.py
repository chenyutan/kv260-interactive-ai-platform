import argparse,json
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
b=json.loads(a.before.read_text(encoding='utf-8'));n=json.loads(a.after.read_text(encoding='utf-8'))
assert b['source_manifest']['source_sha256']==n['source_manifest']['source_sha256']
assert b['processed_frames']==n['processed_frames']==b['expected_frames']==n['expected_frames']
assert len(b['records'])==len(n['records'])==n['expected_frames']
for key in ['model','engine_config','enabled_actions']:
 assert b[key]==n[key], 'Configuration differs: '+key
for r in (b,n):
 assert r['dropped_inference_frames']==0 and r['chronological_unique_frames']
 assert [v['index'] for v in r['records']]==list(range(r['expected_frames']))
with np.load(a.before.with_name(a.before.stem+'-points.npz')) as x,np.load(a.after.with_name(a.after.stem+'-points.npz')) as y:
 assert x['points'].shape==y['points'].shape==(n['expected_frames'],17,3)
 assert x['times'].shape==y['times'].shape==(n['expected_frames'],)
 np.testing.assert_array_equal(x['times'],y['times']);np.testing.assert_array_equal(x['points'],y['points'])
for x,y in zip(b['records'],n['records']):
 for key in ['index','pts','phase','reason','event']:
  if x[key]!=y[key]:raise AssertionError('Mismatch frame %d field %s'%(x['index'],key))
result=dict(frames=n['processed_frames'],configuration_equal=True,complete_chronological_records=True,points_and_confidences_exactly_equal=True,phases_reasons_events_exactly_equal=True,source_file_sha256=n['source_manifest']['source_sha256'],before_wall_fps=b['wall_fps'],after_wall_fps=n['wall_fps'])
a.output.write_text(json.dumps(result,indent=2));print(json.dumps(result))
