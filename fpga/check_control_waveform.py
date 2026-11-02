"""Inspect control VCD response stability and split write-channel pairing."""
from pathlib import Path
import json,re
P=Path(__file__).resolve().parent
names={};values={};changes={};counts=dict(AW=0,W=0,B=0,R=0,start=0,cancel=0,done=0,held_B=0,held_R=0)
aw=w=b=0
def apply():
 global aw,w,b
 before=values.copy();after=before|changes
 if before.get('clk')=='0' and after.get('clk')=='1':
  if before.get('rst')=='1':aw=w=b=0
  elif before.get('rst')=='0':
   for key in ('b','r'):
    if before.get('s_'+key+'valid')=='1' and before.get('s_'+key+'ready')=='0':
     assert after['s_'+key+'valid']=='1'
     assert before['s_'+key+'resp']==after['s_'+key+'resp']
     if key=='r':assert before['s_rdata']==after['s_rdata']
     counts['held_'+key.upper()]+=1
   if before.get('s_awvalid')=='1' and before.get('s_awready')=='1':aw+=1;counts['AW']+=1
   if before.get('s_wvalid')=='1' and before.get('s_wready')=='1':w+=1;counts['W']+=1
   if before.get('s_bvalid')=='1' and before.get('s_bready')=='1':
    b+=1;counts['B']+=1;assert b<=min(aw,w)
   if before.get('s_rvalid')=='1' and before.get('s_rready')=='1':counts['R']+=1
   for key in ('start','cancel','done'):
    if before.get('dma_'+key)=='1':counts[key]+=1
 values.update(changes);changes.clear()
for raw in (P/'sim/control-build/control.vcd').read_text().splitlines():
 line=raw.strip()
 if line.startswith('$var'):
  s=line.split();names[s[3]]=s[4]
 elif line.startswith('#'):apply()
 elif line.startswith('b'):
  value,code=line[1:].split();changes[names[code]]=value
 elif line and line[0] in '01xz' and line[1:] in names:changes[names[line[1:]]]=line[0]
apply()
assert counts['start']==3 and counts['cancel']==1 and counts['held_B']>0 and counts['held_R']>0
log=(P/'sim/control-build/simulation.log').read_text()
assert 'PASS: AXIL control' in log and not re.search('FAIL:|Fatal:',log)
(P/'control-waveform-result.json').write_text(json.dumps(counts,indent=2));print(counts)
