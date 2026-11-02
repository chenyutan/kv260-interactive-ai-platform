"""Build native GLES2 runner without changing system libraries or graphics config."""
import argparse, hashlib, json, os, subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parent;a.output.parent.mkdir(parents=True,exist_ok=True)
    sources=[root/'ink_gl_backend.cpp',root/'runner_scene.cpp']
    inputs=sources+[root/'ink_gl_backend.h',root/'ink_gl_packed_vertex.h',root/'runner_character.h',root/'runner_costume.h',root/'runner_motion.h',root/'runner_imported_character.h',root/'runner_imported_data.h',root/'ink_gl_index_mesh.h',root/'runner_material_mips.h']
    input_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    cmd=['c++','-std=c++17','-O3','-fPIC','-shared','-fvisibility=default','-Wall','-Wextra',
         '-I/usr/include/mali',*map(str,sources),'-o',str(a.output),'-ldl']
    subprocess.run(cmd,check=True)
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=input_hashes[p.name] for p in inputs):
        raise RuntimeError('Native animation sources changed during compilation; no build receipt written')
    receipt=dict(command=cmd,output=str(a.output),bytes=a.output.stat().st_size,
                 sha256=hashlib.sha256(a.output.read_bytes()).hexdigest(),
                 sources=input_hashes)
    a.output.with_suffix('.build.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(receipt))
if __name__=='__main__':main()
