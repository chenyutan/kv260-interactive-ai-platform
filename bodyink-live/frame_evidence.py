"""Opt-in, bounded exact-frame evidence. No camera access or pose modification."""
import copy
import hashlib
import json
import math
import queue
import threading
import time
from pathlib import Path

import numpy as np


class FrameEvidenceWriter:
    def __init__(self, output, *, interval=.5, duration=30., max_frames=60,
                 max_bytes=256*1024*1024, clock=time.monotonic):
        if interval <= 0 or duration <= 0 or max_frames <= 0 or max_bytes <= 0:
            raise ValueError('Evidence limits must be positive')
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=False)
        self.clock=clock;self.started=clock();self.interval=interval;self.duration=duration
        self.max_frames=max_frames;self.max_bytes=max_bytes;self.next_at=self.started
        self.queue=queue.Queue(maxsize=2);self.stop=threading.Event();self.closed=False
        self.accepted=0;self.written=0;self.dropped=0;self.reserved_bytes=0;self.error=None
        self.thread=threading.Thread(target=self._run,name='frame-evidence',daemon=True)
        self.thread.start()

    def submit(self, metadata, raw, points, selection, action, thresholds):
        """Called by the single inference producer with its exact source frame."""
        now=self.clock()
        if (self.closed or self.error or now-self.started>=self.duration or
                now<self.next_at or self.accepted>=self.max_frames):return False
        self.next_at=now+self.interval
        if self.queue.full():self.dropped+=1;return False
        width,height,stride=(int(metadata[k]) for k in ('width','height','stride'))
        if width<=0 or height<=0 or stride<width*2:raise ValueError('Invalid YUYV geometry')
        timestamp=float(metadata['timestamp_seconds'])
        if not math.isfinite(timestamp):raise ValueError('Non-finite source timestamp')
        p=np.asarray(points,np.float32)
        if p.shape!=(17,3):raise ValueError('Expected native 17x3 points')
        th=np.asarray(thresholds,np.float32)
        if th.shape!=(17,) or not np.isfinite(th).all():raise ValueError('Expected 17 thresholds')
        # Freeze data before the caller releases or reuses a mapped capture buffer.
        view=memoryview(raw).cast('B')
        if len(view)!=stride*height:raise ValueError('YUYV length differs from geometry')
        size=len(view)
        if self.reserved_bytes+size>self.max_bytes:return False
        payload=bytes(view)
        record=copy.deepcopy(dict(sequence=int(metadata['sequence']),timestamp_seconds=timestamp,
            width=width,height=height,stride=stride,pixel_format='YUYV',
            selection=selection,action=action,thresholds=th.tolist(),
            native_points_semantics='Unmodified predictions, not human ground truth'))
        # Validate serializability on the producer; invalid evidence is never accepted.
        encoded=json.dumps(record,ensure_ascii=False,allow_nan=False)
        self.queue.put_nowait((self.accepted,payload,p.copy(),encoded))
        self.accepted+=1;self.reserved_bytes+=size
        return True

    def _run(self):
        try:
            while not self.stop.is_set() or not self.queue.empty():
                try:index,raw,points,encoded=self.queue.get(timeout=.1)
                except queue.Empty:continue
                name=f'frame-{index:04d}'
                path=self.output/(name+'.npz')
                # Uncompressed storage avoids competing with inference for compression CPU.
                np.savez(path,yuyv=np.frombuffer(raw,np.uint8),points17=points)
                record=json.loads(encoded);record.update(frame_file=path.name,
                    raw_sha256=hashlib.sha256(raw).hexdigest())
                (self.output/(name+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                self.written+=1;self.queue.task_done()
        except Exception as exc:self.error=f'{type(exc).__name__}: {exc}'

    def close(self):
        if self.closed:return
        self.closed=True;self.stop.set();self.thread.join(10)
        if self.thread.is_alive():self.error=self.error or 'Evidence worker did not finish within 10 seconds'
        report=dict(accepted=self.accepted,written=self.written,dropped=self.dropped,
            reserved_raw_bytes=self.reserved_bytes,error=self.error,
            complete=self.error is None and self.accepted==self.written,
            capture_policy='Opt-in sampled exact-frame evidence; gaps are expected')
        (self.output/'manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
