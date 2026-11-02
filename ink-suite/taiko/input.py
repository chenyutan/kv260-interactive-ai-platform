"""Fresh COCO17 downstrokes, normalized by shoulder distance.

COCO9 is the user's left wrist, COCO10 right. No camera or image reading.
All timing is original observation time, never UI refresh count.
"""
import math


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class DrumInput:
    def __init__(self, confidence=.20):
        self.confidence = confidence
        self.reset()

    def reset(self, minimum_stamp=None):
        # A lifecycle epoch survives tracking-loss resets; late packets from
        # the preceding paused segment may not arm a future downstroke.
        self.minimum_stamp=minimum_stamp if minimum_stamp is not None else getattr(self,'minimum_stamp',None)
        # Tracking loss and GUI lifecycle resets clear motion, not the source
        # high-water mark. A delayed replay from this same backend identity
        # must never become a new raised/downstroke pair after a reset.
        self.high_water_identity = getattr(self, 'high_water_identity', None)
        self.high_water_stamp = getattr(self, 'high_water_stamp', None)
        self.high_water_sequence = getattr(self, 'high_water_sequence', None)
        self.last_stamp = self.last_sequence = self.identity = self.last_seen = self.body = None
        self.body_stamp = None
        self.body_present = False
        self.hands = {}

    def _point(self, points, i):
        try:
            p = points[i]
            if len(p) < 3 or not all(finite(v) for v in p[:3]) or p[2] < self.confidence:
                return None
            if not 0 <= p[0] <= 1 or not 0 <= p[1] <= 1:
                return None
            return p[0] * 16 / 9, p[1]
        except (TypeError, IndexError, KeyError):
            return None

    def _presence(self, points):
        """Presence evidence is independent of a usable downstroke anchor.

        Only current measured core joints count. Wrists alone (including a
        solitary noisy wrist) cannot prolong presence, and no coordinates are
        copied from an earlier frame to manufacture an anchor.
        """
        core = {i: self._point(points, i) for i in (5, 6, 7, 8, 11, 12)}
        for pair, minimum in (((5, 6), .055), ((11, 12), .035)):
            a, b = (core[i] for i in pair)
            if a is not None and b is not None and minimum <= math.dist(a, b) <= 1.:
                return True
        visible = [p for p in core.values() if p is not None]
        if len(visible) < 3:
            return False
        # Three credible core points must include an actual connected limb or
        # trunk, and span a sensible image region; clustered speckles do not
        # count as a person. This can survive one obscured shoulder.
        span = max(math.dist(a, b) for a in visible for b in visible)
        connected = any(core[a] is not None and core[b] is not None and
                        .025 <= math.dist(core[a], core[b]) <= .8
                        for a, b in ((5, 7), (6, 8), (5, 11), (6, 12), (7, 11), (8, 12)))
        return connected and .075 <= span <= 1.3

    def _output(self):
        return dict(valid=False, fresh=False, source_fresh=False, source_age=None, source_stamp=None,
                    body_present=False, body_stamp=self.body_stamp,
                    events=[], status='等待双肩与手腕', hands={},
                    reason='invalid_source', rejections={}, hand_diagnostics={})

    @staticmethod
    def _identity(state):
        selection = state.get('roi_selection') or {}
        if not isinstance(selection, dict):selection={}
        return (state.get('pid'), state.get('person_id', state.get('track_id',
                selection.get('person_id', selection.get('track_id')))))

    def _remember_loss_source(self, state, now):
        """A known source's newer failed frame also precedes older packets.

        Failed/stale frames never arm or prove presence. Remembering their
        timestamp only prevents an older raised pair from being replayed
        after the loss reset. Far-future timestamps cannot poison the mark.
        """
        if not finite(now) or not isinstance(state, dict) or self.high_water_identity is None:
            return
        stamp, sequence = state.get('timestamp_seconds'), state.get('sequence')
        if (finite(stamp) and stamp <= now + .05 and sequence is not None and
                self._identity(state) == self.high_water_identity and
                stamp > self.high_water_stamp and sequence != self.high_water_sequence):
            self.high_water_stamp, self.high_water_sequence = stamp, sequence

    def consume(self, state, now):
        out = self._output()
        if not finite(now) or not isinstance(state, dict):
            self.reset()
            out['body_stamp'] = None
            return out
        suite = state.get('suite') or {}
        if isinstance(suite, dict) and (suite.get('menu') or
                (suite.get('active') and suite.get('mode') not in (None, 'taiko'))):
            self._remember_loss_source(state, now)
            self.reset()
            out['body_stamp'] = None
            out['status'] = '菜单手势 · 已停止击鼓'
            out['reason'] = 'menu_blocked'
            return out
        stamp = state.get('timestamp_seconds')
        if state.get('stale') or state.get('available') is False or not finite(stamp) or state.get('sequence') is None:
            self._remember_loss_source(state, now)
            self.reset()
            out['body_stamp'] = None
            return out
        out['source_age'], out['source_stamp'] = now - stamp, stamp
        before_epoch = self.minimum_stamp is not None and stamp < self.minimum_stamp
        # Shared backend and GTK run on the same board monotonic clock.
        if not -.05 <= now - stamp <= .45:
            self._remember_loss_source(state, now)
            self.reset()
            out['body_stamp'] = None
            out['status'] = '姿态数据已过期'
            out['reason'] = 'source_expired'
            return out
        selection = state.get('roi_selection') or {}
        if not isinstance(selection,dict):selection={}
        identity = self._identity(state)
        discontinuity = state.get('motion_history_discontinuity') or (isinstance(selection, dict) and selection.get('motion_history_discontinuity'))
        identity_changed = self.high_water_identity is not None and self.high_water_identity != identity
        if not identity_changed and self.high_water_stamp is not None:
            replay = (stamp < self.high_water_stamp or
                      (stamp == self.high_water_stamp and state['sequence'] != self.high_water_sequence) or
                      (stamp != self.high_water_stamp and state['sequence'] == self.high_water_sequence))
            if replay:
                self.hands.clear()
                self.last_seen = None
                self.body_present = False
                out['status'] = '姿态顺序异常 · 请重新抬手'
                out['reason'] = 'out_of_order_observation'
                return out
        if identity_changed or (discontinuity and
                (self.high_water_stamp is None or stamp > self.high_water_stamp)):
            self.reset()
            self.identity, self.last_stamp, self.last_sequence = identity, stamp, state['sequence']
            self.high_water_identity, self.high_water_stamp, self.high_water_sequence = identity, stamp, state['sequence']
            out['fresh'] = out['source_fresh'] = True
            out['body_stamp'] = None
            out['status'] = '重新定位 · 请重新抬手'
            out['reason'] = 'identity_discontinuity'
            return out
        if self.high_water_stamp is not None and stamp == self.high_water_stamp:
            out['valid'] = self.last_seen is not None and now - self.last_seen <= .24
            out['body_present'] = (self.body_present and self.body_stamp is not None
                                   and now - self.body_stamp <= .45)
            out['status'] = '等待新姿态' if out['valid'] else '姿态中断'
            out['reason'] = 'duplicate_observation'
            if before_epoch:
                self.hands.clear()
                self.last_seen = None
                out['valid'] = False
                out['status'] = '等待恢复后的新姿态'
                out['reason'] = 'before_resume_epoch'
                return out
            if isinstance(suite, dict) and suite.get('control_active'):
                self.hands.clear()
                self.last_seen = None
                out['valid'] = False
                out['status'] = '菜单手势候选 · 已停止击鼓'
                out['reason'] = 'menu_candidate'
                return out
            if out['valid']:
                # GTK may draw faster than inference produces poses. Keep the
                # last valid hand display between observations, but never
                # reprocess the old trajectory or emit another downstroke.
                out['hands'] = {hand: max(-1., min(2., value['y']))
                                for hand, value in self.hands.items()}
            # Display expiry is not a missing observation. A delayed but
            # still fresh backend can deliver every actual pose .33 s late;
            # intervening 60 Hz GTK redraws must not erase its .067 s motion
            # chain. New observation gaps and source expiry clear it below.
            return out
        if self.last_stamp is not None and stamp - self.last_stamp > .24:
            self.hands.clear()
        self.last_stamp, self.identity, self.last_sequence = stamp, identity, state['sequence']
        self.high_water_identity, self.high_water_stamp, self.high_water_sequence = identity, stamp, state['sequence']
        out['fresh'] = out['source_fresh'] = True
        points = state.get('points17')
        self.body_present = self._presence(points)
        if self.body_present:
            self.body_stamp = stamp
        out['body_present'], out['body_stamp'] = self.body_present, self.body_stamp
        if before_epoch:
            # A normally delayed stream still proves source/person presence
            # during resume. The lifecycle epoch forbids old motion/scoring,
            # not those actual fresh observations: suppress only strike state.
            self.hands.clear()
            self.last_seen = None
            out['status'] = '等待恢复后的新姿态'
            out['reason'] = 'before_resume_epoch'
            return out
        if isinstance(suite, dict) and suite.get('control_active'):
            self.hands.clear()
            self.last_seen = None
            out['status'] = '菜单手势候选 · 已停止击鼓'
            out['reason'] = 'menu_candidate'
            return out
        a, b = self._point(points, 5), self._point(points, 6)
        if a is None or b is None:
            self.hands.clear()
            self.last_seen = None
            out['reason'] = 'shoulder_anchor_missing'
            return out
        scale = math.dist(a, b)
        center = ((a[0] + b[0])/2, (a[1] + b[1])/2)
        if not .055 <= scale <= 1.0:
            self.hands.clear()
            self.last_seen = None
            out['reason'] = 'shoulder_scale_invalid'
            return out
        if self.body is not None and (math.dist(center, self.body[0]) > max(.13, self.body[1] * .65)
                                     or not .65 < scale / self.body[1] < 1.55):
            self.hands.clear()
            self.last_seen = None
            self.body = (center, scale)
            out['reason'] = 'body_anchor_discontinuity'
            return out
        self.body = (center, scale)
        for hand, i in (('L', 9), ('R', 10)):
            wrist = self._point(points, i)
            if wrist is None:
                self.hands.pop(hand, None)
                out['rejections'][hand] = 'wrist_missing'
                out['hand_diagnostics'][hand] = dict(armed=False, local_armed=False,
                    spent=False, y=None, local_top_y=None, rejection='wrist_missing')
                continue
            y = (wrist[1] - center[1]) / scale
            out['hands'][hand] = max(-1., min(2., y))
            old = self.hands.get(hand)
            armed = bool(old and old['armed'])
            relative_armed = bool(old and old.get('relative_armed'))
            spent = bool(old and old.get('spent'))
            peak = old.get('peak', (y, wrist[1], wrist[1]-center[1])) if old else (y, wrist[1], wrist[1]-center[1])
            top = old.get('top', (y, wrist[1], wrist[1]-center[1])) if old else (y, wrist[1], wrist[1]-center[1])
            last_hit = old.get('last_hit') if old else None
            emitted = False
            if old and 0 < stamp - old['at'] <= .24:
                dy = y - old['y']
                dt = stamp - old['at']
                speed = dy / dt
                # A 12 Hz backend samples a genuine fast downstroke across a
                # larger distance than 30 Hz. Bound displacement by observed
                # elapsed time, while retaining an absolute cap and the hit
                # speed ceiling. Abrupt lateral teleports are rejected too.
                jump_limit = max(.80, min(1.60, 15. * dt))
                displacement = math.hypot(wrist[0] - old['raw_x'], wrist[1] - old['raw_y']) / scale
                if abs(dy) > jump_limit or displacement > jump_limit:
                    armed = False
                    relative_armed = False
                    peak = top = (y, wrist[1], wrist[1]-center[1])
                    out['rejections'][hand] = 'wrist_jump'
                # Shoulder-width jitter alone must not move the "virtual
                # wrist" through a hit line. Require observed physical motion
                # both in image Y and relative to the shoulder center.
                raw_speed=(wrist[1]-old['raw_y'])/((stamp-old['at'])*scale)
                relative_speed=((wrist[1]-center[1])-old['offset_y'])/((stamp-old['at'])*scale)
                if hand not in out['rejections']:
                    # Chest-height drumming need not first reach a global
                    # high-hand line. A measured upstroke establishes a local
                    # top, then a measured downstroke consumes that cycle once.
                    # Both the image and shoulder-relative excursion must be
                    # real; changing shoulder width alone cannot arm it.
                    if y > peak[0]:peak = (y, wrist[1], wrist[1]-center[1])
                    if (not relative_armed and speed <= -.55 and raw_speed < -.55 and relative_speed < -.55
                            and peak[0]-y >= .18 and (peak[1]-wrist[1])/scale >= .14
                            and (peak[2]-(wrist[1]-center[1]))/scale >= .14):
                        relative_armed, spent = True, False
                        top = (y, wrist[1], wrist[1]-center[1])
                    if (spent and y <= .30 and speed < 0 and raw_speed < 0 and relative_speed < 0
                            and peak[0]-y >= .18 and (peak[1]-wrist[1])/scale >= .14
                            and (peak[2]-(wrist[1]-center[1]))/scale >= .14):
                        # The legacy high-hand line also accepts a genuinely
                        # slow measured return. It needs accumulated upward
                        # excursion, not the fast reversal arming velocity.
                        # Continuing the same downstroke cannot satisfy this.
                        spent = False
                    if relative_armed and y < top[0]:
                        top = (y, wrist[1], wrist[1]-center[1])
                line_crossed = armed and old['y'] < .68 <= y
                local_line = top[0] + .22
                reversal_crossed = (relative_armed and old['y'] < local_line <= y
                    and (wrist[1]-top[1])/scale >= .16
                    and ((wrist[1]-center[1])-top[2])/scale >= .16)
                cooldown_ok = last_hit is None or stamp-last_hit >= .16
                if (hand not in out['rejections'] and (line_crossed or reversal_crossed) and
                        cooldown_ok and .75 <= speed <= 15 and raw_speed>.55 and relative_speed>.55):
                    hit_line = .68 if line_crossed else local_line
                    fraction = (hit_line - old['y']) / max(1e-9, dy)
                    event_stamp = old['at'] + fraction * (stamp - old['at'])
                    out['events'].append(dict(hand=hand, timestamp=event_stamp,
                        id='%s:%s:%s' % (identity, state['sequence'], hand),
                        path='line' if line_crossed else 'reversal', source_stamp=stamp,
                        previous_source_stamp=old['at'], crossing_line=hit_line,
                        old_normalized_y=old['y'], new_normalized_y=y))
                    armed = relative_armed = False
                    spent, emitted, last_hit = True, True, event_stamp
                    peak = top = (y, wrist[1], wrist[1]-center[1])
                elif old['y'] < .68 <= y and hand not in out['rejections']:
                    out['rejections'][hand] = (('already_consumed' if spent else
                        'local_armed_global_cross' if relative_armed else 'not_armed') if not armed else
                        'strike_speed_invalid' if not .75 <= speed <= 15 else 'anchor_only_motion')
            if y <= .30 and not spent and not emitted and out['rejections'].get(hand) != 'wrist_jump':
                armed = True
            self.hands[hand] = dict(y=y, at=stamp, armed=armed,raw_x=wrist[0],
                                   raw_y=wrist[1],offset_y=wrist[1]-center[1],
                                   relative_armed=relative_armed,spent=spent,peak=peak,top=top,last_hit=last_hit)
            out['hand_diagnostics'][hand] = dict(armed=armed, local_armed=relative_armed,
                spent=spent, y=y, local_top_y=top[0], rejection=out['rejections'].get(hand))
        out['valid'] = bool(out['hands'])
        out['reason'] = 'hands_ready' if out['valid'] else 'wrists_missing'
        out['status'] = '左右手抬起再下挥' if out['valid'] else '等待手腕'
        if out['valid']:
            self.last_seen = stamp
        else:
            self.last_seen = None
        return out
