"""Discrete event budget, not measured throughput. Two slots, latest capture only.

CPU staging/postprocessing share one resource; PL and DPU are separate.
Capture events mean complete raw frames available to the consumer, not exposure.
No displays, DDR contention, camera jitter or Python scheduling are modeled.
"""
import heapq,json
from pathlib import Path

def simulate(stage_ms,pl_ms,dpu_ms=15.65,post_ms=4.59,capture_ms=1000/60,count=600):
 events=[(i*capture_ms,'capture',i,None) for i in range(count)];heapq.heapify(events)
 slots=[None,None];resources={'cpu':None,'pl':None,'dpu':None};latest=None
 jobs={};finished=[];dropped=0;trace=[]
 while events:
  now,kind,ident,slot=heapq.heappop(events)
  if kind=='capture':
   if latest is not None:dropped+=1
   latest=ident
  else:
   job=jobs[ident];resource={'stage':'cpu','pl':'pl','handoff':'cpu','dpu':'dpu','post':'cpu'}[kind]
   assert resources[resource]==ident and slots[slot]==ident
   resources[resource]=None
   job['state']={'stage':'pl_ready','pl':'handoff_ready','handoff':'dpu_ready','dpu':'post_ready','post':'done'}[kind]
   if kind=='post':
    finished.append({'frame':ident,'at_ms':now,'age_ms':now-ident*capture_ms});slots[slot]=None
  # Complete CPU postprocessing first to release buffers; avoid old-frame backlog.
  changed=True
  while changed:
   changed=False
   for state,resource,duration,operation in [('post_ready','cpu',post_ms,'post'),('handoff_ready','cpu',.16,'handoff'),('dpu_ready','dpu',dpu_ms,'dpu'),('pl_ready','pl',pl_ms,'pl')]:
    ready=sorted(i for i,j in jobs.items() if j['state']==state)
    if ready and resources[resource] is None:
     i=ready[0];j=jobs[i];j['state']=operation;resources[resource]=i
     heapq.heappush(events,(now+duration,operation,i,j['slot']));trace.append((resource,now,now+duration,i));changed=True
   if latest is not None and resources['cpu'] is None and None in slots:
    i=latest;latest=None;s=slots.index(None);slots[s]=i
    jobs[i]={'state':'stage','slot':s};resources['cpu']=i
    heapq.heappush(events,(now+stage_ms,'stage',i,s));trace.append(('cpu',now,now+stage_ms,i));changed=True
 assert all(s is None for s in slots) and all(v is None for v in resources.values())
 ids=[f['frame'] for f in finished];assert ids==sorted(set(ids))
 assert len(finished)+dropped==count
 for r in resources:
  intervals=sorted((a,b) for rr,a,b,_ in trace if rr==r)
  assert all(b<=c+1e-8 for (_,b),(c,_) in zip(intervals,intervals[1:]))
 for i in ids:
  spans=sorted((a,b) for _,a,b,k in trace if k==i)
  assert len(spans)==5 and all(b<=c+1e-8 for (_,b),(c,_) in zip(spans,spans[1:]))
 kept=finished[10:-10];ages=sorted(f['age_ms'] for f in kept)
 return {'model_only':True,'processed':len(finished),'dropped_before_staging':dropped,'steady_fps':(len(kept)-1)*1000/(kept[-1]['at_ms']-kept[0]['at_ms']),'mean_raw_ready_to_pose_ms':sum(ages)/len(ages),'p95_raw_ready_to_pose_ms':ages[int(.95*(len(ages)-1))],'serial_work_ms':stage_ms+pl_ms+.16+dpu_ms+post_ms,'slots':2}

if __name__=='__main__':
 p=Path(__file__).resolve().parent;data=json.loads((p/'roi-staging-result.json').read_text())
 results=[]
 for h,pl in [(1080,5.79831),(720,4.55281),(540,3.3085)]:
  row=[r for r in data['rows'] if r['height']==h][1 if h==540 else 0]
  staging=row['sparse_copy_ms']+row['sync_ms']
  results.append({'height':h,'roi':row['roi'],'assumed_pl_ms':pl,'measured_staging_ms':staging,**simulate(staging,pl)})
 # Stress ownership and latest-only dropping with slow and asymmetric stages.
 for args in [(30,4,15,5),(1,40,15,5),(1,4,60,5),(1,4,15,40)]:simulate(*args)
 (p/'pipeline-budget.json').write_text(json.dumps({'limitations':__doc__,'rows':results,'ownership_stress_cases':4},indent=2))
 print(json.dumps(results,indent=2))
