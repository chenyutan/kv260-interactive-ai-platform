"""Generate hand drafts for existing HAND/CHAR videos without touching reviewed work."""
import argparse
from pathlib import Path
from core import *

def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    args=parser.parse_args();project=args.folder.resolve()
    videos=sorted(p for p in project.iterdir() if video_available(p) and parse_name(p)['category'] in ('HAND','CHAR'))
    report=dict(version=VERSION,created_at=now(),completed=[],skipped=[],failed=[])
    for n,v in enumerate(videos,1):
        try:
            previous=load_or_blank(project,v)
            if previous['status']=='reviewed' or any(s.get('source')=='human' or s.get('verified') for s in previous['segments']):
                report['skipped'].append(v.name);continue
            data,arrays=analyze(v,Path(__file__).parent/'models',metadata=previous['metadata'])
            data['notes']=previous.get('notes','');data['events']=previous.get('events',[])
            # Landmark edits belong to the original sampling cache; do not replace it.
            if previous.get('corrections'):
                report['skipped'].append(v.name);continue
            store_analysis(project,data,arrays)
            report['completed'].append(dict(video=v.name,strokes=data['segmentation']['stroke_count'],
                valid_fraction=data['segmentation']['valid_fraction'],hand=data['analysis']['hand_selection']))
            print(f"{n}/{len(videos)} {v.name} strokes={data['segmentation']['stroke_count']}",flush=True)
        except Exception as exc:
            report['failed'].append(dict(video=v.name,error=str(exc)));print('ERROR',v.name,str(exc),flush=True)
        save_json(project/'hand_batch_report.json',report)
    # Merge with the latest project list, preserving all existing entries.
    path=project/'project.json'
    index=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'schema':'inklabel.project.v1','videos':[]}
    seen={str(Path(v).resolve()).casefold() for v in index['videos']}
    for v in videos:
        if str(v).casefold() not in seen: index['videos'].append(str(v));seen.add(str(v).casefold())
    save_json(path,index)
    save_json(project/'hand_batch_report.json',report)

if __name__=='__main__': main()
