from pathlib import Path
import json,re
P=Path(__file__).resolve().parent;names={};values={};changes={};time=0
requests=[];responses=0;outputs=0;held_outputs=0;held_requests=0;remaining=0

def apply():
 global responses,outputs,held_outputs,held_requests,remaining
 before=values.copy();after=before|changes
 if before.get('clk')=='0' and after.get('clk')=='1' and before.get('rst')=='0':
  if before.get('req_valid')=='1' and before.get('req_ready')=='0':
   for name in ('req_valid','req_addr','req_words'):assert before.get(name)==after.get(name),(time,name)
   held_requests+=1
  if before.get('m_valid')=='1' and before.get('m_ready')=='0':
   for name in ('m_rgb','m_int8','m_tag'):assert before.get(name)==after.get(name),(time,name)
   held_outputs+=1
  if before.get('req_valid')=='1' and before.get('req_ready')=='1':
   assert remaining==0,'overlapping row commands'
   remaining=int(before['req_words'],2);requests.append({'address':hex(int(before['req_addr'],2)),'words':remaining})
  if before.get('rsp_valid')=='1' and before.get('rsp_ready')=='1':
   assert remaining>0;remaining-=1;responses+=1
  if before.get('m_valid')=='1' and before.get('m_ready')=='1':
   assert int(before['m_tag'],2)==outputs,'output order';outputs+=1
 values.update(changes);changes.clear()
for raw in (P/'sim/roi-build/roi.vcd').read_text().splitlines():
 line=raw.strip()
 if line.startswith('$var'):
  s=line.split();names[s[3]]=s[4]
 elif line.startswith('#'):apply();time=int(line[1:])
 elif line.startswith('b'):
  value,code=line[1:].split();changes[names[code]]=value
 elif line and line[0] in '01xz' and line[1:] in names:changes[names[line[1:]]]=line[0]
apply()
assert len(requests)>=3 and outputs>=256 and held_outputs>0,(requests,outputs,held_outputs)
report={'duration_us':time/1e6,'row_commands':requests,'response_words':responses,'ordered_outputs':outputs,'stable_output_stall_edges':held_outputs,'stable_request_stall_edges':held_requests,'remaining_response_words_at_window_end':remaining}
(P/'roi-waveform-result.json').write_text(json.dumps(report,indent=2))
log=(P/'sim/roi-build/simulation.log').read_text();assert 'PASS: ROI pipeline' in log and not re.search(r'Fatal:|FAIL:',log)
rows=[dict(zip(['case','stress','cycles','rows','read_bytes'],map(int,m))) for m in re.findall(r'ROI_PASS case=(\d+) stress=(\d+) cycles=(\d+) rows=(\d+) read_bytes=(\d+)',log)]
for row in rows:row['simulated_ms_at_100mhz']=row['cycles']/100000
(P/'roi-timing-result.json').write_text(json.dumps({'results':rows,'note':'functional simulation with 32-bit row-response model, not board DDR measurement'},indent=2))
print(json.dumps(report));print(json.dumps(rows))
