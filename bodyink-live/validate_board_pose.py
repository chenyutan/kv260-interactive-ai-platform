import json,time
from pathlib import Path
import numpy as np
from movenet import MoveNet,YuyvPreprocessor
root=Path(__file__).resolve().parent
net=MoveNet(root/'models/movenet_ntd_pt.xmodel',root/'models/movenet_ntd_pt.prototxt')
rows=json.loads((root/'validation/inputs.json').read_text()); results=[]
for row in rows:
    rgb=np.fromfile(root/'validation'/(row['stem']+'.rgb'),np.uint8).reshape(192,192,3)
    points,timing=net.predict(rgb)
    if 'roi' in row:
        x,y,w,h=row['roi']; valid=(points[:,:2]>=0).all(1)
        points[valid,:2]=(points[valid,:2]*[w,h]+[x,y])/[row['width'],row['height']]
    results.append(dict(**row,points=points.tolist(),timing=timing))
pre=YuyvPreprocessor()
raw=(root/'validation/synthetic.yuyv').read_bytes()
for w,h in [(32,24),(17,13),(192,192)]:
    pre.resize(raw,32,24,64,(w,h)).tofile(root/f'validation/synthetic-{w}x{h}.rgb')
report=dict(model=net.info,frames=results)
(root/'validation/board-pose.json').write_text(json.dumps(report,indent=2))
print(json.dumps(dict(model=net.info,frames=[dict(stem=r['stem'],timing=r['timing'],min_body_score=min(p[2] for p in r['points'][5:13])) for r in results])))
