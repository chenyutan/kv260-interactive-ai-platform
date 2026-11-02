"""Build fresh XRT metadata using the new HWH and unchanged DPU kernel ABI.

Original argument/memory schemas are reusable, but the address, IRQ, UUID,
board description and build identity are regenerated. Preprocess is polled
MMIO, not a second XRT-scheduled AP_CTRL_HS kernel.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import datetime
import xml.etree.ElementTree as ET

P = Path(__file__).resolve().parent
GEN = P.parent/'build/usb/bodyink_usb.gen/sources_1/bd/kv260_ispMipiRx_vcu_DP'
HWH = GEN / 'hw_handoff/kv260_ispMipiRx_vcu_DP.hwh'
tree = ET.parse(HWH)
mods = {m.get('INSTANCE'): m for m in tree.findall('.//MODULE')}
def param(mod, name):
    return mods[mod].find(f"./PARAMETERS/PARAMETER[@NAME='{name}']").get('VALUE')
def signal(mod, name):
    return mods[mod].find(f"./PORTS/PORT[@NAME='{name}']").get('SIGNAME')
assert int(param('DPUCZDX8G_1', 'C_BASEADDR'), 0) == 0xa0000000
assert int(param('preprocess', 'C_BASEADDR'), 0) == 0x81000000
assert signal('DPUCZDX8G_1', 'interrupt') == signal('accel_irq', 'In0')
assert signal('preprocess', 'irq') == signal('accel_irq', 'In1')
assert signal('accel_irq', 'dout') == signal('PS_0', 'pl_ps_irq0')
arch = json.loads((GEN/'ip/kv260_ispMipiRx_vcu_DP_DPUCZDX8G_1_0/arch.json').read_text())
assert arch['fingerprint'] == '0x101000016010407'
ip = json.loads((P/'original_ip_layout.json').read_text())
assert ip['ip_layout']['m_count'] == '1'
ip['ip_layout']['m_ip_data'][0].update(m_base_address='0xa0000000', m_interrupt_id='0')
(P/'ip_layout.json').write_text(json.dumps(ip, indent=2))
xml = ET.parse(P/'original_embedded.xml')
xml.getroot().set('name', 'bodyink-usb-dpu-preprocess')
board = xml.find('.//board'); board.set('name', 'xilinx.com:kv260_som:1.4')
core = xml.find('.//core'); core.set('numComputeUnits', '1')
xml.find('.//addrRemap').set('base', '0xA0000000')
for item in xml.findall('.//connection'):
    if item.get('dstPort') == 'S_AXI_CONTROL':
        item.set('srcPort', 'axi_ic_accel_ctrl_M00_AXI')
xml.write(P/'embedded.xml', encoding='utf-8', xml_declaration=True)
build = json.loads((P/'original_build.json').read_text())
def refresh(value):
    if isinstance(value, dict):
        return {k: ([] if k == 'actual_resources' else refresh(v)) for k,v in value.items()}
    if isinstance(value, list):
        return [refresh(v) for v in value]
    if isinstance(value, str):
        return value.replace('0xa0010000','0xa0000000').replace('0x00A0010000','0x00A0000000').replace('M01_AXI','M00_AXI').replace(':1.3', ':1.4')
    return value
build = refresh(build)
stamp = datetime.datetime.now().isoformat(timespec='seconds')
build['build_metadata']['dsa']['generated_by'] = dict(name='Vivado', version='2022.2', cl='3671981', time_stamp=stamp)
build['build_metadata']['dsa']['description'] = 'BodyInk fixed USB platform with DPU and polled preprocessing DMA'
build['build_metadata']['xclbin']['generated_by'] = dict(name='xclbinutil', version='2.14.0', cl='0', time_stamp=stamp, options='BodyInk build_metadata.py; manual Vivado integration; verified HWH address and interrupt mapping')
(P/'build.json').write_text(json.dumps(build, indent=2))
exe = os.environ.get('XCLBINUTIL', 'xclbinutil')
env = dict(os.environ)

sections = {'MEM_TOPOLOGY': ('JSON','original_memory.json'),
            'IP_LAYOUT': ('JSON','ip_layout.json'),
            'CONNECTIVITY': ('JSON','connectivity.json'),
            'GROUP_CONNECTIVITY': ('JSON','group_connectivity.json'),
            'GROUP_TOPOLOGY': ('JSON','group_topology.json'),
            'EMBEDDED_METADATA': ('RAW','embedded.xml'),
            'BUILD_METADATA': ('JSON','build.json')}
args = [exe, '--key-value', 'SYS:mode:flat',
        '--key-value', 'SYS:PlatformVBNV:bodyink_kv260_usb_dpu_preprocess_1_0',
        '--output', str(P/'bodyink-usb-pl.xclbin'), '--force']
for name, (fmt, file) in sections.items():
    args += ['--add-section', f'{name}:{fmt}:{file}']
result = subprocess.run(args, cwd=P, env=env, text=True, capture_output=True)
(P/'xclbin-build.log').write_text(result.stdout + result.stderr)
result.check_returncode()
report = subprocess.run([exe, '--info', '--input', str(P/'bodyink-usb-pl.xclbin')],
                        env=env, text=True, capture_output=True, check=True)
(P/'xclbin-info.txt').write_text(report.stdout)
assert '0xa0000000' in report.stdout
manifest = {'hwh_sha256': hashlib.sha256(HWH.read_bytes()).hexdigest(),
            'dpu_fingerprint': arch['fingerprint'], 'dpu_base': '0xa0000000',
            'dpu_irq_index': 0, 'preprocess_base': '0x81000000',
            'preprocess_irq_enabled': False,
            'note': 'Metadata prepared; bitstream and board validation required.'}
(P/'metadata-manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=2))
