"""Explicit full rebuild requested by the operator, with a pre-run annotation snapshot."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from core import *

def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    parser.add_argument('--replace-reviewed',action='store_true',required=True)
    args=parser.parse_args();root=args.folder.resolve()
    videos=sorted(v for v in root.iterdir() if video_available(v))
    backup=root/'annotation_snapshots'/datetime.now().strftime('before_1_4_%Y%m%d_%H%M%S')
    backup.mkdir(parents=True)
    if (root/'annotations').exists():shutil.copytree(root/'annotations',backup/'annotations')
    if (root/'project.json').exists():shutil.copy2(root/'project.json',backup/'project.json')
    report=dict(version=VERSION,started_at=now(),backup=str(backup),completed=[],failed=[])
    def process(v):
        old=load_or_blank(root,v)
        cache=old.get('keypoints_file')
        if cache and (root/cache).is_file():
            with np.load(root/cache) as z:a={k:z[k] for k in z.files}
            d,a['speed']=resegment(old,a);mode='cached'
        else:
            if old.get('corrections'):raise ValueError('缓存缺失且有人工关键点修正，未覆盖')
            d,a=analyze(v,Path(__file__).parent/'models',metadata=old['metadata'])
            d['events']=old.get('events',[]);d['notes']=old.get('notes','');mode='analyzed'
        d['status']='draft';d.pop('reviewed_at',None)
        if validate(d):raise ValueError(str(validate(d)))
        return v,d,a,mode
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(process,v):v for v in videos}
        for future in as_completed(pending):
            v=pending[future]
            try:
                _,d,a,mode=future.result();store_analysis(root,d,a)
                row=dict(video=v.name,mode=mode,category=d['metadata']['category'],
                    strokes=d.get('segmentation',{}).get('stroke_count'),
                    bridges=len(d.get('segmentation',{}).get('inferred_state_intervals',[])))
                report['completed'].append(row)
                print(f"{len(report['completed'])}/{len(videos)} {v.name} {mode} strokes={row['strokes']} bridges={row['bridges']}",flush=True)
            except Exception as exc:
                report['failed'].append(dict(video=v.name,error=str(exc)));print('ERROR',v.name,str(exc),flush=True)
            save_json(root/'rebuild_1.4_report.json',report,False)
    index_path=root/'project.json'
    index=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'schema':'inklabel.project.v1','videos':[]}
    seen={str(Path(v).resolve()).casefold() for v in index['videos']}
    for v in videos:
        if str(v).casefold() not in seen:index['videos'].append(str(v));seen.add(str(v).casefold())
    save_json(index_path,index)
    report['finished_at']=now();save_json(root/'rebuild_1.4_report.json',report,False)
    print('FINISHED',len(report['completed']),'FAILED',len(report['failed']),flush=True)

if __name__=='__main__':main()
