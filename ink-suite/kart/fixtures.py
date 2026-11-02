"""Deterministic render fixtures only; never synthesize live pose input."""
from .engine import TRACK,KartGame

NAMES=('starting-grid','bend','village','items')

def apply_fixture(game,name):
    if name not in NAMES:raise ValueError('Unknown kart scene fixture')
    game.restart();game.countdown=0.;game.state='running';game.elapsed=18.;game.clock=18.
    if name=='starting-grid':distance=0.
    elif name=='bend':distance=max(range(80,680,5),key=lambda d:abs(TRACK.curvature(d+25)))
    elif name=='village':distance=TRACK.length*.29
    else:distance=TRACK.length*.57
    for i,r in enumerate(game.karts):
        r.s=distance+(i//2)*8+3*i;r.x=(0.,-2.7,2.7,-3.8,3.7,1.2)[i];r.speed=29.
    game.player.item='pulse';game.distance=distance;game.rank=6
    game.lap=min(3,1+int(distance/TRACK.length))
    if name=='items':
        game.player.shield=5.;game.player.boost=2.;game.player.drift=1.
        game.player.item='boost';game.karts[2].boost=2.
        game.projectiles=[dict(s=distance+19,x=-2.2,owner=0,target=1,life=3.)]
        for i,p in enumerate(game.pickups[:6]):p.update(s=(distance+22+i*9)%TRACK.length,x=(-3.6,0,3.6)[i%3],timer=0.)
    return game

def snapshot(name):
    game=apply_fixture(KartGame(),name);out=game.snapshot()
    out['ui']=dict(control_mode='gamepad',notice='',tracking_paused=False)
    return out
