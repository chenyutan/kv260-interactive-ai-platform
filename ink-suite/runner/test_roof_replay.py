"""Fixed distance input replays for real, continuous rooftop transfers."""
import unittest
from .engine import RunnerGame


def roof_replay(mode=2, speed=21., boots=False, seed=1, jump=True):
    g=RunnerGame(seed);g.start();g._next_wave_distance=1e9
    g.START_SPEED=g.MAX_SPEED=speed
    g._wave_id=mode;g._spawn_wave()
    route=next(e for e in g.drain_events() if e['kind']=='wave')
    lane=route['route_lane'];g.player_lane=lane;g.player_x=g._lane_start=float(lane)
    for item in g.entities:item.z-=64.
    # Powers are not silently used to make this diagnostic invulnerable.
    g.entities[:]=[e for e in g.entities if e.kind not in g.POWERUPS]
    if boots:g.boots_time=100.
    edges=(27.6,52.2) if mode==1 else (27.6,52.2,76.8)
    landing_lanes=[next(e.lane for e in g.entities if e.kind=='cart' and abs(e.z-(edge+3.))<.01) for edge in edges]
    commands=[];samples=[];next_jump=0
    for frame in range(int((route['route_span']+2)/speed*120)):
        actions={}
        # Fixed travel thresholds: input depends on the route, never on
        # secretly reading collisions/grounded state to auto-correct a miss.
        if jump and next_jump<len(edges) and g.distance>=edges[next_jump]-3.:
            target=landing_lanes[next_jump]
            actions={'jump':True,'move':(target>g.player_lane)-(target<g.player_lane)}
            commands.append(dict(frame=frame,distance=g.distance,actions=actions.copy()))
            next_jump+=1
        g.update(1/120,actions)
        if frame%4==0:samples.append(g.snapshot())
    return g,commands,samples


class RoofRouteReplayTests(unittest.TestCase):
    def test_adjacent_transfer_and_true_three_metre_gaps(self):
        for seed in range(12):
            g,commands,_=roof_replay(seed=seed)
            self.assertEqual(len(commands),3)
            self.assertTrue(all(abs(c['actions']['move'])==1 for c in commands))
            self.assertEqual(g.lives,3)

    def test_deliberate_repeated_jumps_land_at_all_play_speeds(self):
        for mode in (1,2):
            for speed in (11.,16.,21.):
                for boots in (False,True):
                    with self.subTest(mode=mode,speed=speed,boots=boots):
                        g,commands,samples=roof_replay(mode,speed,boots)
                        self.assertEqual(g.lives,3)
                        self.assertEqual(g.counts.get('jump'),len(commands))
                        self.assertEqual(g.counts.get('hit',0),0)
                        self.assertGreater(g.roof_distance,25)
                        self.assertGreater(g.coins,10)
                        # At least one stable supported interval after each
                        # landing, before the next fresh input is sent.
                        for a,b in zip(commands,commands[1:]):
                            self.assertTrue(any(a['distance']+speed*.96<s['distance']<b['distance'] and
                                abs(s['player_y']-g.ROOF_HEIGHT)<.02 and s['jump_height']==0 for s in samples))

    def test_no_input_is_not_turned_into_automatic_gap_jump(self):
        g,commands,_=roof_replay(mode=1,speed=16,jump=False)
        self.assertEqual(commands,[])
        self.assertEqual(g.counts.get('jump',0),0)
        self.assertGreater(g.counts.get('hit',0),0)
        self.assertLess(g.lives,3)

    def test_long_route_reserves_its_full_footprint(self):
        g=RunnerGame();g._wave_id=2;g._spawn_wave()
        wave=next(e for e in g.drain_events() if e['kind']=='wave')
        ends=[e.z-g.SPAWN_DISTANCE+g.CART_LENGTH for e in g.entities if e.kind=='cart']
        self.assertGreaterEqual(wave['route_span'],max(ends)-1e-6)
        self.assertGreater(wave['next_spacing'],max(ends)+10)
        self.assertLess(len(g.entities),g.MAX_ENTITIES)


if __name__=='__main__':unittest.main()

