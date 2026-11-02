"""Render actual classifier events from fresh_replay; no label-driven drawing."""
import argparse,json,time
from pathlib import Path
from ink_renderer import InkRenderer

def render(events,output,theme='mixed'):
    painter=InkRenderer();records=[]
    for event in events:
        event=dict(event)
        if 'calibration' not in event:event['calibration']=event['speed_calibration']
        start=time.monotonic();result=painter.add(event,theme)
        records.append({'action':event['action'],'render_ms':(time.monotonic()-start)*1000,'result':result})
    painter.save(output)
    return {'events':records,'event_count':len(painter.events),'output':str(output)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('events',type=Path);p.add_argument('output',type=Path);p.add_argument('--theme',default='mixed');args=p.parse_args()
    result=render(json.loads(args.events.read_text(encoding='utf-8')),args.output,args.theme)
    args.output.with_suffix('.validation.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    print(json.dumps({'event_count':result['event_count'],'output':result['output'],
        'events':[{'action':r['action'],'render_ms':r['render_ms'],'accepted':r['result']['accepted']} for r in result['events']]},ensure_ascii=False))
