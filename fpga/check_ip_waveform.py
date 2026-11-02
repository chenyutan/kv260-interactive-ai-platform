"""Numerical AXI waveform checks, plus full-run scoreboard summaries."""
from pathlib import Path
import json,re
P=Path(__file__).resolve().parent;names={};values={};changes={};time=0
read_left=write_left=0;write_response=False;reads=writes=rbeats=wbeats=bcount=0;held=0
write_index=0;seen=[]
gold=[int(s,16)>>24 for s in (P/'roi-vectors/expected0.hex').read_text().split()]
def check_word(index,data):
 expected=0
 for j in range(4):
  offset=index*4+j;expected|=((gold[offset//3]>>(8*(offset%3)))&255)<<(8*j)
 assert data==expected,(index,hex(data),hex(expected))
def apply():
 global read_left,write_left,write_response,reads,writes,rbeats,wbeats,bcount,held,write_index
 before=values.copy();after=before|changes
 if before.get('clk')=='0' and after.get('clk')=='1' and before.get('rst')=='0':
  for channel,payload in [('ar',('araddr','arlen')),('aw',('awaddr','awlen')),('w',('wdata','wlast'))]:
   if before.get(channel+'valid')=='1' and before.get(channel+'ready')=='0':
    assert after.get(channel+'valid')=='1'
    for name in payload:assert before.get(name)==after.get(name),(time,'stalled',name)
    held+=1
  if before.get('arvalid')=='1' and before.get('arready')=='1':
   assert read_left==0;read_left=int(before['arlen'],2)+1;addr=int(before['araddr'],2);assert (addr&4095)+read_left*4<=4096
   reads+=1
  if before.get('rvalid')=='1' and before.get('rready')=='1':
   assert read_left>0 and int(before['rlast'],2)==(read_left==1);read_left-=1;rbeats+=1
  if before.get('awvalid')=='1' and before.get('awready')=='1':
   assert write_left==0 and not write_response;write_left=int(before['awlen'],2)+1;addr=int(before['awaddr'],2)
   assert (addr&4095)+write_left*4<=4096
   write_index=(addr-0x200000ffc)//4;writes+=1;seen.append({'address':hex(addr),'beats':write_left})
  if before.get('wvalid')=='1' and before.get('wready')=='1':
   assert write_left>0 and int(before['wlast'],2)==(write_left==1);check_word(write_index,int(before['wdata'],2));write_index+=1;write_left-=1;wbeats+=1
   if write_left==0:write_response=True
  if before.get('bvalid')=='1' and before.get('bready')=='1':
   assert write_response and write_left==0;write_response=False;bcount+=1
 values.update(changes);changes.clear()
for raw in (P/'sim/ip-build/dma.vcd').read_text().splitlines():
 line=raw.strip()
 if line.startswith('$var'):
  s=line.split();names[s[3]]=s[4]
 elif line.startswith('#'):apply();time=int(line[1:])
 elif line.startswith('b'):
  value,code=line[1:].split();changes[names[code]]=value
 elif line and line[0] in '01xz' and line[1:] in names:changes[names[line[1:]]]=line[0]
apply()
assert wbeats>256 and held>0 and seen[0]['beats']==1
report={'window_us':time/1e6,'AR':reads,'R_beats':rbeats,'AW':writes,'W_beats_byte_checked':wbeats,'B':bcount,'stable_stalled_edges':held,'write_bursts':seen,'boundary_and_last_checked':True}
(P/'ip-waveform-result.json').write_text(json.dumps(report,indent=2))
results=[]
for name in ('ip-build','ip-build-7'):
 log=(P/'sim'/name/'simulation.log').read_text()
 assert 'PASS: AXIL integrated DMA' in log and not re.search(r'FAIL:|Fatal:',log)
 pattern=r'DMA_PASS case=(\d+) mode=(\d+) max_burst=(\d+) cycles=(\d+) read_bytes=(\d+) write_bytes=(\d+) AR=(\d+) AW=(\d+)'
 rows=[dict(zip(('case','mode','max_burst','cycles','read_bytes','write_bytes','AR','AW'),map(int,m))) for m in re.findall(pattern,log)]
 for row in rows:row['model_ms_at_100mhz']=row['cycles']/100000
 results.append({'build':name,'cases':rows,'pass_line':re.search(r'PASS: AXIL integrated DMA[^\n]+',log)[0]})
(P/'ip-result.json').write_text(json.dumps(results,indent=2));print(json.dumps(report));print(json.dumps(results))

