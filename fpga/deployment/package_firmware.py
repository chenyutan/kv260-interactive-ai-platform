"""Only package a routed, timing-clean bitstream; retain source hashes."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess

P=Path(__file__).resolve().parent
B=P.parent/'build/buildusb'
timing=(B/'routed_timing.rpt').read_text()
heading=timing.index('WNS(ns)')
values=None
for line in timing[heading:].splitlines()[1:]:
    fields=line.split()
    if len(fields)>=12 and re.fullmatch(r'-?\d+\.\d+',fields[0]):
        values=fields;break
assert values is not None, 'Routed timing summary absent'
wns,whs,wpws=map(float,(values[0],values[4],values[8]))
assert min(wns,whs,wpws)>=0, f'Timing not met: {wns=} {whs=} {wpws=}'
log=Path(os.environ.get('BODYINK_BUILD_LOG', str(P.parent/'vivado.log'))).read_text(errors='replace')
assert 'write_bitstream completed successfully' in log, 'Bitstream completion not confirmed'
assert (B/'bodyink.bit').stat().st_size>1000000
(B/'bodyink.bif').write_text('all:\n{\n bodyink.bit\n}\n')
env=dict(os.environ)

result=subprocess.run([os.environ.get('BOOTGEN','bootgen'),
                       '-arch','zynqmp','-image','bodyink.bif','-process_bitstream','bin','-w','on'],
                      cwd=B,env=env,text=True,capture_output=True)
(P/'bootgen.log').write_text(result.stdout+result.stderr)
result.check_returncode()
assert (B/'bodyink.bit.bin').stat().st_size>1000000
shutil.copy2(B/'bodyink.bit.bin',P/'bodyink-usb-pl.bin')
hashes={name:hashlib.sha256((P/name).read_bytes()).hexdigest()
        for name in ('bodyink-usb-pl.bin','bodyink-usb-pl.dtbo','bodyink-usb-pl.xclbin')}
(P/'firmware.sha256').write_bytes(''.join(f'{sha}  {name}\n' for name,sha in hashes.items()).encode('ascii'))
manifest=json.loads((P/'metadata-manifest.json').read_text())
manifest.update(timing=dict(wns_ns=wns,whs_ns=whs,wpws_ns=wpws),files=hashes,
                bit_sha256=hashlib.sha256((B/'bodyink.bit').read_bytes()).hexdigest(),
                note='Timing-clean bitstream packaged; actual board validation still required.')
(P/'firmware-manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps(manifest,indent=2))
