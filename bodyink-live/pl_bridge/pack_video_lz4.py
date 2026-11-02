"""Decode every source frame once into independently verified lossless LZ4 blocks."""
import argparse,hashlib,json,time
from pathlib import Path
import av
import numpy as np
import lz4.block

def pack(video,output):
    output.mkdir(parents=True,exist_ok=True)
    target=output/'frames.lz4blocks'
    if target.exists() or (output/'manifest-lz4.json').exists():
        raise FileExistsError('Existing pack; choose a new directory')
    start=time.monotonic();entries=[];digest=hashlib.sha256()
    with av.open(str(video)) as container, target.with_suffix('.tmp').open('xb') as out:
        stream=container.streams.video[0];w,h=stream.width,stream.height;declared=stream.frames
        if w%2:raise ValueError('YUYV requires even width')
        for frame in container.decode(stream):
            if frame.pts is None:raise ValueError('Missing PTS')
            pts=float(frame.pts*frame.time_base)
            if entries and pts<=entries[-1]['pts']:raise ValueError('Non-monotonic PTS')
            image=frame.reformat(width=w,height=h,format='yuyv422');plane=image.planes[0]
            pixels=np.frombuffer(plane,np.uint8).reshape(h,plane.line_size)[:,:w*2].tobytes()
            block=lz4.block.compress(pixels,store_size=False)
            if lz4.block.decompress(block,uncompressed_size=len(pixels))!=pixels:
                raise AssertionError('Lossless compression roundtrip failed')
            entries.append(dict(offset=out.tell(),size=len(block),pts=pts,raw_sha256=hashlib.sha256(pixels).hexdigest()))
            out.write(block);digest.update(block)
    if not entries:raise ValueError('No decoded frames')
    target.with_suffix('.tmp').replace(target)
    manifest=dict(schema='bodyink.full_yuyv_replay.v1',source=str(video.resolve()),
        source_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),width=w,height=h,stride=w*2,
        frame_count=len(entries),container_reported_frames=declared,declared_count_matches_decoded=declared==len(entries),
        first_pts=entries[0]['pts'],last_pts=entries[-1]['pts'],chunks=[dict(first_index=0,len=len(entries))],
        pack_format='direct-lz4-only; chunks describe frame count, not NPZ files',
        decode='PyAV decoded every frame, swscale YUYV422 conversion, no spatial resize or temporal subsampling',
        av_version=av.__version__,pack_seconds=time.monotonic()-start,
        lz4=dict(file=target.name,bytes=target.stat().st_size,sha256=digest.hexdigest(),frames=entries,
                 host_all_decoded_pixels_equal=True))
    (output/'manifest-lz4.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return {k:manifest[k] for k in ('source','frame_count','pack_seconds') }|{'bytes':target.stat().st_size}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('video',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    print(json.dumps(pack(a.video,a.output),indent=2))
