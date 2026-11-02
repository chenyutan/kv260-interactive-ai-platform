import numpy as np
from action_engine import _stability_radius

def reference(poses):
    center=np.median(poses,axis=0)
    return float(np.percentile(np.sqrt(np.mean((poses-center)**2,axis=(1,2))),90))

def test_rank_selection_matches_numpy_exactly():
    rng=np.random.default_rng(35030)
    for n in range(4,151):
        for amplitude in [0.,1e-7,.025,.06,1.,1000.]:
            poses=rng.normal(size=(n,8,2))*amplitude
            # Include plateaus and repeated observations.
            poses[::3]=poses[0]
            assert _stability_radius(poses)==reference(poses)

def test_boundary_decisions_match_without_tolerance():
    rng=np.random.default_rng(35)
    for n in range(4,65):
        poses=rng.normal(size=(n,8,2))
        expected=reference(poses)
        actual=_stability_radius(poses)
        for radius in [expected,np.nextafter(expected,-np.inf),np.nextafter(expected,np.inf)]:
            assert (actual<=radius)==(expected<=radius)
