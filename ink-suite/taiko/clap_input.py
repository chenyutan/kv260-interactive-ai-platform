"""Measured chest-level wrist convergence, independent of downward drumming.

COCO17 cannot observe palm contact. The contact line is a wrist-distance
proxy, crossed by both hands moving inward, using original source time.
"""
import math
from .input import DrumInput


class ClapInput:
    OPEN = .80
    CONTACT = .75
    EXCURSION = .18
    HAND_EXCURSION = .05

    def __init__(self, confidence=.20):
        self.guard = DrumInput(confidence)
        self.reset()

    def reset(self, minimum_stamp=None):
        self.guard.reset(minimum_stamp=minimum_stamp)
        self.previous = self.origin = None
        self.last_hit = None
        self.control = dict(valid=False, armed=False, gap=None, events=[], reason='waiting_wrists')

    def consume(self, state, now, validated=None):
        # The panel supplies its existing strict DrumInput result: there is
        # no second model, pose read, or independently extended freshness lease.
        control = self.guard.consume(state, now) if validated is None else validated
        out = dict(valid=False, armed=bool(self.origin), gap=None, closing=False, events=[],
                   reason=control.get('reason', 'invalid_source'))
        if not control.get('fresh'):
            if control.get('reason') != 'duplicate_observation':
                self.previous = self.origin = None
            elif self.previous is not None:
                out.update(valid=bool(control.get('valid')), gap=self.previous['gap'])
            out['armed'] = bool(self.origin)
            self.control = out
            return out
        if not control.get('valid') or control.get('reason') != 'hands_ready':
            self.previous = self.origin = None
            self.control = out
            return out
        points = state.get('points17')
        a, b, left, right = (self.guard._point(points, i) for i in (5, 6, 9, 10))
        if any(p is None for p in (a, b, left, right)):
            self.previous = self.origin = None
            out['reason'] = 'clap_wrists_missing'
            self.control = out
            return out
        scale = math.dist(a, b)
        center = ((a[0]+b[0])/2, (a[1]+b[1])/2)
        local = tuple(((p[0]-center[0])/scale, (p[1]-center[1])/scale) for p in (left, right))
        gap = math.dist(*local)
        stamp = control['source_stamp']
        row = dict(at=stamp, raw=(left, right), local=local, gap=gap, scale=scale)
        out.update(valid=True, gap=gap, reason='clap_ready')
        old = self.previous
        chest = (all(-.65 <= p[1] <= 1.5 for p in local) and
                 abs(local[0][1]-local[1][1]) <= .50 and
                 abs((local[0][0]+local[1][0])/2) <= .85)
        if not chest:
            self.origin = None
            out['reason'] = 'outside_clap_band'
        elif old and 0 < stamp-old['at'] <= .24:
            dt = stamp-old['at']
            moves = [math.dist(row['raw'][i], old['raw'][i])/scale for i in (0, 1)]
            if max(moves) > max(.8, min(1.4, 15*dt)):
                self.origin = None
                out['reason'] = 'clap_wrist_jump'
            elif self.origin and stamp-self.origin['at'] <= .85:
                origin = self.origin
                direction = 1 if origin['local'][0][0] > origin['local'][1][0] else -1
                inward = [direction*(origin['raw'][0][0]-left[0])/scale,
                          direction*(right[0]-origin['raw'][1][0])/scale]
                relative = [direction*(origin['local'][0][0]-local[0][0]),
                            direction*(local[1][0]-origin['local'][1][0])]
                downward = [max(0., (row['raw'][i][1]-origin['raw'][i][1])/scale,
                                local[i][1]-origin['local'][i][1]) for i in (0, 1)]
                # Before contact, slight convergence during a mostly downward
                # drum stroke is insufficient to reserve both drum lanes.
                # Allow a measured diagonal clap up to 2:1 down/inward travel;
                # actual contact below remains independently authoritative.
                inward_preparation = all(min(inward[i], relative[i]) >= .5*downward[i]
                                         for i in (0, 1))
                closing = (old['gap']-gap)/dt
                # Recognize inward preparation before contact, so a diagonal
                # clap cannot leak an earlier downstroke into the drum lanes.
                excursion = origin['gap']-gap
                out['closing'] = (.55 <= closing <= 18 and excursion >= .12
                                  and min(inward) >= .04 and min(relative) >= .04
                                  and inward_preparation)
                # A modest opening still needs a real measured excursion.
                # If it reaches CONTACT before that evidence exists, retain
                # a lower crossing line instead of losing the entire clap.
                contact = min(self.CONTACT, origin['gap']-self.EXCURSION)
                crossed = old['gap'] > contact >= gap
                out['motion'] = dict(excursion=excursion, inward=inward,
                                     relative=relative, closing_speed=closing,
                                     downward=downward, inward_preparation=inward_preparation,
                                     contact_line=contact)
                cooldown = self.last_hit is None or stamp-self.last_hit >= .28
                if (crossed and cooldown and .55 <= closing <= 18 and
                        excursion >= self.EXCURSION and min(inward) >= self.HAND_EXCURSION
                        and min(relative) >= self.HAND_EXCURSION):
                    fraction = (old['gap']-contact)/(old['gap']-gap)
                    event_stamp = old['at']+fraction*dt
                    event = dict(hand='C', timestamp=event_stamp,
                        id='%s:%s:C' % (DrumInput._identity(state), state['sequence']),
                        path='clap', source_stamp=stamp, previous_source_stamp=old['at'],
                        crossing_gap=contact, old_wrist_gap=old['gap'], new_wrist_gap=gap)
                    out['events'] = [event]
                    out['reason'] = 'clap_crossing'
                    self.last_hit = event_stamp
                    self.origin = None
            elif self.origin:
                self.origin = None
        else:
            self.origin = None
        # Preserve the widest measured opening through the whole short cycle:
        # one hand may start inward before the other. Replacing the origin on
        # every still-open frame erases that hand's real motion. Reopening
        # beyond it refreshes the cycle; contact at entry still cannot score.
        if (chest and gap >= self.OPEN and out['reason'] != 'clap_wrist_jump'
                and (self.origin is None or gap >= self.origin['gap'])):
            self.origin = row
        self.previous = row
        out['armed'] = bool(self.origin)
        self.control = out
        return out
