import os
from pathlib import Path
import numpy as np
from roi_staging import RoiStaging

def main():
    os.environ['BODYINK_ROI_COPY_LIBRARY']=str(Path(__file__).with_name('libroi_copy.host.so'))
    rng=np.random.default_rng(35032);count=0
    for width,height,stride in [(32,31,80),(960,540,1920),(1280,720,2560),(1920,1080,3840)]:
        source=rng.integers(0,256,height*stride,dtype=np.uint8)
        rois=[(0,0,width,height),(1,1,width-1,height-1),(width-3,height-3,3,3)]
        for _ in range(30):
            x=int(rng.integers(width));y=int(rng.integers(height))
            rois.append((x,y,int(rng.integers(1,width-x+1)),int(rng.integers(1,height-y+1))))
        for roi in rois:
            plan=RoiStaging(width,height,stride,roi)
            destination=np.full(source.size+64,93,np.uint8)
            pw,ph,ps,pr=plan.pack(source,destination[:source.size])
            np.testing.assert_array_equal(destination[source.size:],93)
            for row in plan.rows:
                src=source[int(row)*stride+plan.begin:int(row)*stride+plan.end]
                dst=destination[(int(row)-plan.first_row)*ps:(int(row)-plan.first_row+1)*ps]
                np.testing.assert_array_equal(src,dst)
            # Translate every bilinear input coordinate and chroma pair.
            x,y,w,h=roi
            for original,packed,extent,limit,packed_limit,origin in [(x,pr[0],w,width,pw,plan.begin//2),(y,0,h,height,ph,y)]:
                q=np.clip(original*512+(2*np.arange(256)+1)*extent-256,original*512,(original+extent-1)*512)
                pq=np.clip(packed*512+(2*np.arange(256)+1)*extent-256,packed*512,(packed+extent-1)*512)
                np.testing.assert_array_equal(q-origin*512,pq)
                np.testing.assert_array_equal(np.minimum(q//512+1,limit-1)-origin,np.minimum(pq//512+1,packed_limit-1))
            count+=1
    print(f'PASS: {count} packed ROI spans, chroma alignment, bilinear neighbours and guards')

if __name__=='__main__':main()
