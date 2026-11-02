"""Build the independent GLES2 kart library; no renderer changes to other games."""
import argparse,hashlib,json,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parent;a.output.parent.mkdir(parents=True,exist_ok=True)
    sources=[root/'ink_gl_backend.cpp',root/'kart_scene.cpp']
    inputs=sources+[root/'ink_gl_backend.h',root/'ink_gl_packed_vertex.h',root/'ink_gl_index_mesh.h']
    hashes={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in inputs}
    command=['c++','-std=c++17','-O3','-fPIC','-shared','-fvisibility=default','-Wall','-Wextra',
             '-I/usr/include/mali',*map(str,sources),'-o',str(a.output),'-ldl']
    subprocess.run(command,check=True)
    if any(hashlib.sha256(x.read_bytes()).hexdigest()!=hashes[x.name] for x in inputs):
        raise RuntimeError('Sources changed during compilation')
    receipt=dict(command=command,sources=hashes,output=str(a.output),bytes=a.output.stat().st_size,
                 sha256=hashlib.sha256(a.output.read_bytes()).hexdigest())
    a.output.with_suffix('.build.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt))
if __name__=='__main__':main()
