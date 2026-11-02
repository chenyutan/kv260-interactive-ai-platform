"""Real PL/DPU verification with saved YUYV frames only. No camera access."""
import argparse
import gc
import json
import os
from pathlib import Path
import pwd
import sys
import time
import numpy as np
from pl_preprocess import Preprocessor

ROOT = Path('/home/ubuntu/bodyink-live')
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frames', type=int, default=6)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Open the verified MMIO window as root, then use the production user's
    # XRT/VART locks and file ownership for every model/BO operation.
    pp = Preprocessor()
    if os.geteuid() == 0:
        account = pwd.getpwnam('ubuntu')
        os.setgroups([account.pw_gid]); os.setgid(account.pw_gid); os.setuid(account.pw_uid)
    from hourglass import Hourglass
    from movenet import YuyvPreprocessor
    from hourglass_select import rois
    from xrt_buffer import Device
    net = Hourglass(ROOT/'models/hourglass-pe_mpii.xmodel')
    cpu = YuyvPreprocessor()
    device = Device()
    rows = []
    reference = np.load(ROOT/'pl-deploy-0914/old-dpu-reference.npz')
    tag = 0
    try:
        for h,w in [(1080,1920),(720,1280),(540,960)]:
            frames = np.load(ROOT/f'resolution-compare/{h}.npz')['raw']
            source = device.alloc(h*w*2,True)
            output = device.alloc(pp.OUTPUT_BYTES+64,True)
            src = source.array(); dst = output.array()
            try:
                for frame_idx in np.linspace(0,len(frames)-1,args.frames,dtype=int):
                    raw = np.ascontiguousarray(frames[frame_idx]).reshape(-1)
                    for roi_idx,roi in enumerate(rois(w,h)):
                        rgb = cpu.resize(raw,w,h,w*2,(256,256),roi=roi)
                        net.predict(rgb)
                        expected_input = net.input.copy()
                        expected_output = net.output.copy()
                        key = f'{h}_{frame_idx}_{roi_idx}'
                        np.testing.assert_array_equal(expected_input, reference[key+'_input'])
                        np.testing.assert_array_equal(expected_output, reference[key+'_output'])
                        dst.fill(0xa5); output.sync(0)
                        t0 = time.monotonic()
                        np.copyto(src,raw); source.sync(0)
                        t1 = time.monotonic()
                        tag += 1
                        pp.submit(source,output,w,h,w*2,roi,tag)
                        status = pp.wait()
                        t2 = time.monotonic()
                        output.sync(1)
                        np.testing.assert_array_equal(dst[pp.OUTPUT_BYTES:],0xa5)
                        tensor = dst[:pp.OUTPUT_BYTES].view(np.int8).reshape(net.input.shape)
                        mismatch = int(np.count_nonzero(tensor != expected_input))
                        if mismatch:
                            np.savez(args.output.with_suffix('.failure.npz'), actual=tensor.copy(), expected=expected_input, frame=raw, roi=roi)
                            raise AssertionError(f'PL tensor mismatch: {mismatch} bytes, {h=} {frame_idx=} {roi=}')
                        np.copyto(net.input,tensor); del tensor
                        t3 = time.monotonic()
                        job = net.runner.execute_async([net.input],[net.output])
                        rc = net.runner.wait(job)
                        if rc not in (None,0): raise RuntimeError(f'DPU wait: {rc}')
                        t4 = time.monotonic()
                        np.testing.assert_array_equal(net.output,expected_output)
                        row = dict(height=h,frame=int(frame_idx),roi=roi_idx,
                                   input_equal=True,heatmaps_equal=True,guard_equal=True,old_dpu_equal=True,
                                   **status, staging_ms=(t1-t0)*1000,pl_wall_ms=(t2-t1)*1000,
                                   sync_compare_copy_ms=(t3-t2)*1000,dpu_ms=(t4-t3)*1000)
                        rows.append(row)
                        print(json.dumps(row),flush=True)
            finally:
                if pp.active_buffers is None:
                    del src,dst; gc.collect(); source.close(); output.close()
    except BaseException:
        if pp.active_buffers is not None:
            # Do not let process teardown release an in-flight DMA allocation.
            # The supervising SSH session must reset/unload PL first.
            print('FATAL_DMA_NOT_DRAINED: holding BOs; supervisor must reset PL',flush=True)
            while True: time.sleep(1)
        raise
    finally:
        if pp.active_buffers is None:
            pp.close(); device.close()
    result = dict(actual_pl_dma_tested=True,live_camera_read=False,cases=len(rows),rows=rows)
    args.output.write_text(json.dumps(result,indent=2))
    print('PASS: recorded-frame PL tensors and DPU heatmaps are bit-exact',flush=True)

if __name__ == '__main__': main()
