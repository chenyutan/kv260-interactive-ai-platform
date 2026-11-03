import numpy as np
from roi_staging import RoiStaging

def main():
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
            plan.copy(source,destination[:source.size])
            np.testing.assert_array_equal(destination[source.size:],93)
            # Independent reference: derive the two bilinear sample rows, and
            # verify every byte including untouched rows/padding at the original stride.
            x,y,w,h=roi
            qy=np.clip(y*512+(2*np.arange(256)+1)*h-256,y*512,(y+h-1)*512)
            rows=np.unique(np.concatenate([qy//512,np.minimum(qy//512+1,height-1)]))
            begin=x//2*4;end=(min(x+w,width-1)//2+1)*4
            expected=np.full_like(source,93).reshape(height,stride)
            expected[rows,begin:end]=source.reshape(height,stride)[rows,begin:end]
            np.testing.assert_array_equal(destination[:source.size],expected.ravel())
            count+=1
    print(f'PASS: {count} native row-staging cases, stride padding and guard bytes')

if __name__=='__main__':main()
