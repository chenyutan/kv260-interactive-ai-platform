"""Report whole-file throughput and quality observations without hiding failures."""
import argparse,collections,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('reports',nargs='+',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
rows=[]
for path in a.reports:
 r=json.loads(path.read_text(encoding='utf-8'));n=r['expected_frames']
 assert r['processed_frames']==len(r['records'])==n
 assert r['dropped_inference_frames']==0
 assert [v['index'] for v in r['records']]==list(range(n))
 rows.append(dict(report=str(path),source=r['source_manifest']['source'],frames=n,wall_fps=r['wall_fps'],
  wall_seconds=r['wall_seconds_including_chunk_loading'],passes_35_fps=r['wall_fps']>35,
  dual_roi_frames=sum(len(v['roi_timing'])>1 for v in r['records']),
  reasons=dict(collections.Counter(v.get('reason') for v in r['records'])),artwork_events=r['artwork_events'],
  accuracy_note='These are model outputs, not human-verified ground truth',enabled_actions=r['enabled_actions']))
out=dict(files=rows,total_frames=sum(x['frames'] for x in rows),
 weighted_wall_fps=sum(x['frames'] for x in rows)/sum(x['wall_seconds'] for x in rows),
 all_files_pass_35_fps=all(x['passes_35_fps'] for x in rows),
 scope='Only the explicitly listed complete files; not entire dataset coverage')
a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
