"""Offline v2/v3 visual and timing audit. Reads existing files only."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image, ImageDraw
from ink_filter import InkFilter


def stats(values):
    return dict(mean_ms=float(np.mean(values)), p50_ms=float(np.median(values)),
                p95_ms=float(np.percentile(values,95)), max_ms=float(max(values)))


def comparison(left, right, left_label, right_label, path):
    canvas=Image.new('RGB',(1280,394),'#eee9dc')
    canvas.paste(Image.fromarray(left),(0,34));canvas.paste(Image.fromarray(right),(640,34))
    draw=ImageDraw.Draw(canvas)
    draw.text((12,10),left_label,fill='#31473b');draw.text((652,10),right_label,fill='#31473b')
    canvas.save(path,quality=94)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--iterations',type=int,default=90)
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    paths=sorted(args.input.glob('*.jpg'))+sorted(args.input.glob('*.png'))
    if not paths:raise ValueError('No saved input images')
    images=[np.asarray(Image.open(p).convert('RGB').resize((640,360))) for p in paths]
    points=np.zeros((17,3),np.float32)
    points[5]=(.49,.31,.9);points[6]=(.60,.31,.9)
    result=dict(width=640,height=360,threads=1,source='existing_saved_images_only',files=[p.name for p in paths])
    with InkFilter(640,360) as filt:
        baseline=InkFilter(640,360,args.baseline) if args.baseline else None
        try:
            for path,rgb in zip(paths,images):
                art=filt.render(rgb)
                assert art.shape==rgb.shape and art.dtype==np.uint8
                assert np.array_equal(art,filt.render(rgb)), 'Static-frame shimmer'
                Image.fromarray(art).save(args.output/(path.stem+'-v3.png'))
                comparison(rgb,art,'Existing RGB frame | unchanged geometry','Colour ink v3 | no pose supplied',args.output/(path.stem+'-original-v3.jpg'))
                if baseline:
                    old=baseline.render(rgb)
                    Image.fromarray(old).save(args.output/(path.stem+'-v2.png'))
                    comparison(old,art,'v2 | grey ink wash','v3 | natural colour ink wash',args.output/(path.stem+'-v2-v3.jpg'))
            for label,engine,pose in [('v3_no_pose',filt,None),('v3_with_pose',filt,points),('v2_no_pose',baseline,None)]:
                if engine is None:continue
                for n in range(8):engine.render(images[n%len(images)],pose)
                wall=[];cpu=[];native=[];native_cpu=[]
                out=np.empty_like(images[0])
                for n in range(args.iterations):
                    t=time.perf_counter();c=time.thread_time()
                    engine.render(images[n%len(images)],pose,out=out)
                    cpu.append((time.thread_time()-c)*1000);wall.append((time.perf_counter()-t)*1000)
                    native.append(engine.timing()['wall_ms'])
                    if 'thread_cpu_ms' in engine.timing():native_cpu.append(engine.timing()['thread_cpu_ms'])
                result[label]=dict(version=engine.version,iterations=len(wall),wall=stats(wall),thread_cpu=stats(cpu),
                    stages=('gray','guides','face','render') if engine is filt else ('gray','smooth','wide','render'),
                    native_stage_wall_mean_ms=np.mean(native,axis=0).tolist(),
                    native_stage_cpu_mean_ms=np.mean(native_cpu,axis=0).tolist() if native_cpu else None)
            invalid=np.zeros((360,640,3),np.float32)
            try:filt.render(invalid);raise AssertionError('Invalid type accepted')
            except ValueError:pass
            for pose in (np.full((17,3),np.nan,np.float32),np.zeros((17,3),np.float32),np.full((17,3),np.inf,np.float32)):
                assert np.array_equal(filt.render(images[0],pose),filt.render(images[0])), 'Invalid pose changes rendering'
            before=images[0].copy();out=np.empty_like(before)
            assert filt.render(images[0],out=out) is out
            assert np.array_equal(before,images[0]), 'Input modified'
            assert np.array_equal(out,filt.render(images[0])), 'Buffer reuse differs'
            try:filt.render(images[0],out=images[0]);raise AssertionError('Aliasing output accepted')
            except ValueError:pass
            noncontiguous=images[0][:,::-1,:]
            assert np.array_equal(filt.render(noncontiguous),filt.render(np.ascontiguousarray(noncontiguous)))
            # Successive unrelated frames must not feed prior artwork back into
            # the next source: A -> B -> A gives the same A byte for byte.
            a=filt.render(images[0]);filt.render(images[-1]);assert np.array_equal(a,filt.render(images[0]))
            result.update(deterministic=True,invalid_pose_fallback=True,invalid_dtype_rejected=True,
                          output_reuse=True,input_unchanged=True,noncontiguous_input=True,no_temporal_feedback=True)
        finally:
            if baseline:baseline.close()
    (args.output/'benchmark.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
