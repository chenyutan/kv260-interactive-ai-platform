"""Finite acceptance measurement; no fixed/artificial FPS counters."""
import argparse,json,os,time,urllib.request
from pathlib import Path

def cpu():
    values=[int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return {'total':sum(values),'idle':values[3]+values[4]}
def memory(pid):
    try:
        data=Path(f'/proc/{pid}/status').read_text().splitlines()
        return {key:int(next(s.split()[1] for s in data if s.startswith(key+':'))) for key in ['VmRSS','VmHWM','Threads']}
    except (OSError,StopIteration):return None
def process_ticks(pid):
    fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
    return int(fields[11])+int(fields[12])
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=600);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    start=time.monotonic();deadline=start+args.seconds;before=cpu();rows=[]
    while True:
        row={'elapsed_seconds':time.monotonic()-start,'cpu':cpu()}
        try:
            with urllib.request.urlopen('http://127.0.0.1:8088/api/state',timeout=3) as f:row['runtime']=json.load(f)
            row['camera']=json.loads(Path('/run/bodyink-camera/status.json').read_text())
            row['temperature_c']=[int(p.read_text())/1000 for p in Path('/sys/class/thermal').glob('thermal_zone*/temp')]
            row['loadavg']=Path('/proc/loadavg').read_text().split()[:3]
            pid=row['runtime']['pid'];row['memory']=memory(pid);row['process_ticks']=process_ticks(pid)
            row['camera_memory']=memory(row['camera']['pid'])
            row['usb_uvc_stats']={str(p):p.read_text() for p in Path('/sys/kernel/debug/usb/uvcvideo').glob('*/stats')}
        except Exception as e:row['error']=str(e)
        rows.append(row)
        report={'duration_seconds':time.monotonic()-start,'clock_ticks_per_second':os.sysconf('SC_CLK_TCK'),'samples':rows,'cpu_busy_percent':100*(1-(row['cpu']['idle']-before['idle'])/max(1,row['cpu']['total']-before['total']))}
        temporary=args.output.with_suffix('.tmp');temporary.write_text(json.dumps(report,indent=2));temporary.replace(args.output)
        if time.monotonic()>=deadline:break
        time.sleep(min(5,max(0,deadline-time.monotonic())))
    print(json.dumps({k:v for k,v in report.items() if k!='samples'}))
if __name__=='__main__':main()
