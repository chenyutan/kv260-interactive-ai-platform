"""Check actual VCD transitions, not only simulator PASS output."""
from pathlib import Path
import json
P=Path(__file__).resolve().parent;path=P/'sim/build/pipeline.vcd'
names={};values={};changes={};scope=[];time=0;ins=outs=holds=resets=0;tags=[];sample_count=0

def apply():
 global ins,outs,holds,resets,sample_count
 if not changes:return
 before=values.copy();after=before|changes
 if before.get('clk')=='0' and after.get('clk')=='1':
  sample_count+=1
  if before.get('rst')=='1':
   assert after.get('m_valid')=='0','valid survived reset';resets+=1
  else:
   if before.get('m_valid')=='1' and before.get('m_ready')=='0':
    for name in ('m_tag','m_rgb','m_int8'):
     assert before.get(name)==after.get(name),(time,'stalled output changed',name)
    holds+=1
   if before.get('s_valid')=='1' and before.get('s_ready')=='1':ins+=1
   if before.get('m_valid')=='1' and before.get('m_ready')=='1':
    outs+=1;tags.append(int(before['m_tag'],2))
 values.update(changes);changes.clear()
for raw in path.read_text().splitlines():
 line=raw.strip()
 if line.startswith('$var'):
  fields=line.split();names[fields[3]]=fields[4]
 elif line.startswith('#'):
  apply();time=int(line[1:])
 elif line.startswith('b'):
  value,code=line[1:].split();changes[names[code]]=value
 elif line and line[0] in '01xz' and line[1:] in names:changes[names[line[1:]]]=line[0]
apply()
assert holds>20 and outs>1000 and resets>=6,(holds,outs,resets)
assert tags[:1024]==list(range(1024)),'stress transfer order'
assert tags[1024:]==list(range(len(tags)-1024)),'continuous transfer order'
result={'vcd':str(path.name),'duration_us':time/1e6,'rising_edges':sample_count,'input_handshakes':ins,'output_handshakes':outs,'stable_backpressure_edges':holds,'reset_edges':resets,'order_verified':True,'note':'25 us waveform window; full-run scoreboard is in simulation.log'}
(P/'waveform-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
