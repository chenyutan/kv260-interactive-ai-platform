"""Separate process entry: reuse validated camera/Hourglass/PL, no ink classifier.

Only start after stopping bodyink-live.service. Does not overwrite app.py.
"""
import sys
import numpy as np
sys.path.insert(0,'/home/ubuntu/bodyink-live')
import app
from fight_evidence import install as install_fight_evidence
install_fight_evidence(app)
Original=app.ActionEngine
class PoseOnly(Original):
    def update(self,points,timestamp,width,height):
        q=np.asarray(points);valid=q.shape==(17,3) and bool(np.isfinite(q[[9,10]]).all() and (q[[9,10],2]>=.2).any())
        return dict(phase='idle',quality={'valid':valid},event=None,reason='fruit_pose_only',engine='fruit_pose_only_v1')
app.ActionEngine=PoseOnly
if __name__=='__main__':app.main()
